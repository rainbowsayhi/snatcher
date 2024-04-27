"""
The course selector base module.
    1. The `BaseCourseSelector` class:
        The base class of all course selector
        -- `set_kch_id` method:
            Setting course number id. Must be rewritten.
        -- `set_xkkz_id` method:
            Setting 'xkkz' id. Must be rewritten.
        -- `set_jxb_ids` method:
            Setting 'jxb' id. Must be rewritten.
        -- `prepare_for_selecting` method:
            It will be called before sending a request. Must be rewritten.
        -- `simulate_request` method:
            The specific logic of selecting course. Must be rewritten.
        -- `select` method:
            A method for outer caller. Must be rewritten.

    2. The `CourseSelector` class:
        The father class of course selector.
"""
from typing import Union

from snatcher.conf import settings
from snatcher.db.mysql import (
    create_failed_data,
    create_selected_data,
)
from snatcher.mail import send_email
from snatcher.db.redis import (
    RunningLogs,
    AsyncRunningLogs
    # ParseStudentID
)


class BaseCourseSelector:
    # 开课类型代码，公选课 10，体育课 05，主修课程 01（英语、思政类），特殊课程 09，其他特殊课程 11
    course_type: str = ''
    term: int = settings.TERM
    select_course_year: int = settings.SELECT_COURSE_YEAR  # 选课学年码

    def __init__(self, username: str):
        self.username = username  # 学号
        # self.parser = ParseStudentID(username)  # 解析学号
        # 获取教学班ids所需的表单数据
        # self.get_jxb_ids_data = {
        #     'bklx_id': 0,  # 补考类型id
        #     'xqh_id': 3,  # 校区号id
        #     'jg_id': '206',  # 学院id
        #     'zyfx_id': 'wfx',  # 专业方向 无方向
        #     'njdm_id': self.parser.year,  # 年级ID，必须  2022
        #     'bh_id': self.parser.class_id,  # 班级ID  0425221
        #     'xbm': 1,  # 性别 男 1  女 2
        #     'xslbdm': 'wlb',  # 学生类别代码 无类别
        #     'mzm': 13,  # 民族码
        #     'xz': 4,  # 学制
        #     'ccdm': 3,  # 层次代码
        #     'xsbj': 4,  # 学生标记，国内学生 4
        #     'xkxnm': self.select_course_year,  # 选课学年码
        #     'xkxqm': self.term,  # 选课学期码（上下学期，上学期 3，下学期 12）
        #     'kklxdm': '',  # 开课类型代码，公选课10，体育课05、主修课程01，特殊课程09
        #     'kch_id': '',  # 课程id
        #     'xkkz_id': ''  # 选课的时间，课程的类型（主修、体育、特殊、通识）
        # }
        self.get_jxb_ids_data = {
            'bklx_id': 0,  # 补考类型id
            'njdm_id': '20' + username[:2],  # 年级ID，必须  2022
            'xkxnm': self.select_course_year,  # 选课学年码
            'xkxqm': self.term,  # 选课学期码（上下学期，上学期 3，下学期 12）
            'kklxdm': '',  # 开课类型代码，公选课10，体育课05、主修课程01，特殊课程09
            'kch_id': '',  # 课程id
            'xkkz_id': ''  # 选课的时间，课程的类型（主修、体育、特殊、通识）
        }
        # 选课api所需的表单数据
        self.select_course_data = {
            'jxb_ids': '',
            'kch_id': '',
            'qz': 0  # 权重
        }
        self.timeout = settings.TIMEOUT
        self.real_name = None
        self.log_key = None
        self.log: Union[RunningLogs, AsyncRunningLogs, None] = None
        self.session = None
        self.cookies = None
        self.select_course_api = None
        self.index_url = None
        self.jxb_ids_api = None
        self.base_url = None
        self.port = None
        self.kch_id = None  # 课程ID
        self.jxb_ids = None  # 教学班ids
        self.xkkz_id = None

    def set_xkkz_id(self):
        """set xkkz id"""
        raise NotImplementedError('rewrite me')

    def set_jxb_ids(self):
        """set jxb id"""
        raise NotImplementedError('rewrite me')

    def prepare_for_selecting(self):
        """one by one call: set_xkkz_id, set_jxb_ids"""
        raise NotImplementedError('rewrite me')

    def simulate_request(self):
        """simulating browser send request"""
        raise NotImplementedError('rewrite me')

    def select(self):
        """outer caller please calling me"""
        raise NotImplementedError('rewrite me')

    def update_or_set_cookie(self, cookie_string: str, port: str):
        """update or set the relative information"""
        if not cookie_string or not port:
            return
        self.cookies = {'JSESSIONID': cookie_string}
        base_url = ''.join(['http:', '//10.3.132.', port, '/jwglxt'])
        self.select_course_api = base_url + '/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html?gnmkdm=N253512'  # 选课api
        self.index_url = base_url + '/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512&layout=default'  # 选课首页
        self.jxb_ids_api = base_url + '/xsxk/zzxkyzbjk_cxJxbWithKchZzxkYzb.html?gnmkdm=N253512'  # 获取教学班ids的api
        self.base_url = base_url
        self.port = port

    def update_selector_info(self, course_name: str, course_id: str, email: str):
        """update relative information"""
        self.real_name = course_name
        self.kch_id = course_id
        self.log_key = f'{self.username}-{course_name}'
        # self.log = RunningLogs(f'{self.username}-{course_name}')
        create_selected_data(self.username, email, course_name, self.log_key)

    def mark_failed(self, failed_reason: str):
        """create a fail data into mysql"""
        send_email(
            '1834763300@qq.com',
            self.username,
            self.real_name,
            False,
            failed_reason
        )
        create_failed_data(
            self.username,
            self.real_name,
            self.log.key,
            failed_reason,
            self.port
        )


class CourseSelector(BaseCourseSelector):
    """
    The father class of all course selector.
    Including synchronous course selector and asynchronous course selector.
    """
