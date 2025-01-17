import asyncio

import jwt
from fastapi import (
    APIRouter,
    Depends,
    WebSocketException,
    WebSocket,
    Query,
    Path,
    Body,
    Request
)
from starlette.websockets import WebSocketDisconnect
from redis.asyncio.client import PubSub
from arq import ArqRedis
from arq.jobs import Job

from .validators import (
    SubmittedValidator,
    FailureValidator,
    EnergyValidator,
    LoginValidator,
    PEValidator,
    PCValidator
)
from backend.utils.user import identity_validator, login, get_security_key
from backend.routes.vpn.validators import CourseTypeEnum
from backend.response import SnatcherResponse, ResponseCodes
from snatcher.storage.cache import (
    runtime_logs_generator,
    AIORedis,
    CHANNEL_NAME,
    parse_message
)
from snatcher.conf import settings
from snatcher.storage.mongo import collections, decrypt_fuel, update_fuel_status, BSONObjectId


router = APIRouter(prefix='/manage', tags=['后台管理'])


@router.get('/submitted/{page}', summary='查询所有已提交选课程数据', dependencies=[Depends(identity_validator)])
def get_all_selected_data(page: int = Path(ge=1)):
    submitted_collection = collections['submitted']
    cursor, total = submitted_collection.query(page, 20, sort=[('updated_at', -1)])
    results = []
    for data in cursor:
        validator = SubmittedValidator(**data)
        results.append(validator.model_dump())
    return SnatcherResponse(ResponseCodes.OK, {'results': results, 'total': total})


@router.get('/failure/{page}', summary='查询所有选课失败数据', dependencies=[Depends(identity_validator)])
def get_failed_data(page: int = Path(ge=1)):
    failure_collection = collections['failure']
    cursor, total = failure_collection.query(page, 20, sort=[('created_at', -1)])
    results = []
    for data in cursor:
        validator = FailureValidator(**data)
        results.append(validator.model_dump())
    return SnatcherResponse(ResponseCodes.OK, {'results': results, 'total': total})


@router.get('/energy/{page}', summary='查询所有能量', dependencies=[Depends(identity_validator)])
def get_verify_code(page: int):
    energy_collection = collections['energy']
    cursor, total = energy_collection.query(page, 20, sort=[('created_at', -1)])
    results = []
    for data in cursor:
        validator = EnergyValidator(**data)
        results.append(validator.model_dump())
    return SnatcherResponse(ResponseCodes.OK, {'results': results, 'total': total})


@router.post('/fuel', summary='生成燃料', dependencies=[Depends(identity_validator)])
def create_verify_code(username: str = Body(embed=True)):
    energy_collection = collections['energy']
    key = get_security_key('fuel')
    fuel = energy_collection.create(username, key)
    return SnatcherResponse(ResponseCodes.OK, {'fuel': fuel})


@router.get('/pc/{page}', summary='查询公选课列表', dependencies=[Depends(identity_validator)])
def get_pc_course(page: int = Path(ge=1)):
    pc_collection = collections['pc']
    cursor, total = pc_collection.query(page, 20)
    results = []
    for data in cursor:
        validator = PCValidator(**data)
        results.append(validator.model_dump())
    return SnatcherResponse(ResponseCodes.OK, {'results': results, 'total': total})


@router.get('/pe/{page}', summary='查询体育课列表', dependencies=[Depends(identity_validator)])
def get_pe_course(page: int = 1):
    pe_collection = collections['pe']
    cursor, total = pe_collection.query(page, 20)
    results = []
    for data in cursor:
        validator = PEValidator(**data)
        results.append(validator.model_dump())
    return SnatcherResponse(ResponseCodes.OK, {'results': results, 'total': total})


@router.post('/login', summary='超级管理员登录')
def superuser_login(form: LoginValidator):
    token = login(form.username, form.password)
    if not token:
        return SnatcherResponse(ResponseCodes.LOGIN_FAILED)
    response = SnatcherResponse(ResponseCodes.OK)
    response.headers.setdefault('Access-Control-Expose-Headers', 'Authorization')
    response.headers.setdefault('Authorization', token)
    return response


@router.websocket('/monitor')
async def monitor_logs_change(websocket: WebSocket, token: str = Query(default='')):
    """Monitor the change of all logs by Redis pub-sub mode."""
    if not token:
        raise WebSocketException(1008)
    secret = get_security_key('jwt')
    try:
        jwt.decode(token, secret, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        raise WebSocketException(1008)
    except jwt.InvalidTokenError:
        raise WebSocketException(1008)

    await websocket.accept()

    # Sending the initializing data to client by every batch.
    batch_logs = []
    for log in runtime_logs_generator():
        batch_logs.append(log)
        if len(batch_logs) == 10:
            await websocket.send_json({'msg': batch_logs, 'status': 1})
            batch_logs.clear()
    if batch_logs:
        await websocket.send_json({'msg': batch_logs, 'status': 1})

    conn = AIORedis(decode_responses=True)
    p: PubSub | None = None

    async def subscribe():
        """Starting to monitor the change of all logs."""
        nonlocal p

        p = conn.pubsub()
        await p.subscribe(CHANNEL_NAME)
        await p.parse_response()  # Throwing the first message.

        while True:
            messages = await p.parse_response()
            if messages[0] == 'unsubscribe':
                break
            msg = parse_message(messages[-1])
            await websocket.send_json({'msg': msg, 'status': 2})

    async def receive(ws: WebSocket):
        """Expecting raise the `WebSocketDisconnect`, so that to close subscribing."""
        while True:
            await ws.receive_text()

    try:
        await asyncio.gather(
            asyncio.create_task(subscribe()),
            asyncio.create_task(receive(websocket))
        )
    except WebSocketDisconnect:
        await p.unsubscribe()
        await conn.aclose()


@router.post('/task/selection', summary='发送已选择课程人数任务', dependencies=[Depends(identity_validator)])
async def send_query_selected_number_task(
    request: Request,
    course_type: CourseTypeEnum = Body(),
    username: str = Body(),
    cookie: str = Body(),
    port: str = Body(),
    frequency: int = Body()
):
    arq_redis: ArqRedis = getattr(request.state, 'arq-redis')
    await arq_redis.enqueue_job('query_selected_number_task', course_type.value, username, cookie, port, frequency)
    return SnatcherResponse(ResponseCodes.OK)


@router.delete('/task/selection', summary='停止执行已选择课程人数任务', dependencies=[Depends(identity_validator)])
async def stop_query_selected_number_task(course_type: CourseTypeEnum = Body(embed=True)):
    async with AIORedis(**settings.DATABASES['redis']['public']) as conn:
        await conn.set(course_type.value + '_stop', '1')
    return SnatcherResponse(ResponseCodes.OK)


@router.delete('/task/select-course', summary='取消选课', dependencies=[Depends(identity_validator)])
async def abort_select_course(
    request: Request,
    username: str = Body(),
    fuel: str = Body(pattern=r'^[A-Za-z0-9/+]{67}=$')
):
    arq_redis: ArqRedis = getattr(request.state, 'arq-redis')
    key = get_security_key('fuel')
    fuel_id = decrypt_fuel(fuel, key)
    job_id = username + '-' + fuel_id
    job = Job(job_id=job_id, redis=arq_redis)
    result = await job.abort()
    if result:
        update_fuel_status(BSONObjectId(fuel_id), 'unused')
        return SnatcherResponse(ResponseCodes.TASK_CANCELED_SUCCESS)
    return SnatcherResponse(ResponseCodes.TASK_CANCELED_FAILED)
