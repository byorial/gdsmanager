# -*- coding: utf-8 -*-
#########################################################
# python
import os
import traceback

# third-party
import requests
from flask import Blueprint, request, send_file, redirect, render_template

# sjva 공용
from framework import app, path_data, check_api, py_urllib, SystemModelSetting
from framework.logger import get_logger
from framework.util import Util
from framework.common.plugin import get_model_setting, Logic, default_route

# 패키지
#########################################################
class P(object):
    package_name = __name__.split('.')[0]
    logger = get_logger(package_name)
    blueprint = Blueprint(package_name, package_name, url_prefix='/%s' %  package_name, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))

    menu = {
        'main' : [package_name, '구드공관리'],
        'sub' : [
            ['base', '구드공관리'],['manual','메뉴얼'],['log', '로그']
        ],
        'category' : 'service',
        'sub2' : {
            'base': [
                ['setting','설정'],['browser', '탐색'],['watch', '감시대상목록'],['changes','갱신목록']
            ],
            'manual': [
                ['manual/changelog.md','변경내역'],['README.md', '메뉴얼']
            ],
        },
    }
    plugin_info = {
        'version' : '0.1.1.5',
        'name' : package_name,
        'category_name' : 'service',
        'icon' : '',
        'developer' : u'orial',
        'description' : u'구드공 바로보기 변경사항 조회 및 갱신 플러그인',
        'home' : 'https://github.com/byorial/%s' % package_name,
        'more' : '',
    }
    ModelSetting = get_model_setting(package_name, logger)
    logic = None
    module_list = None
    home_module = 'base'


def initialize():
    try:
        app.config['SQLALCHEMY_BINDS'][P.package_name] = 'sqlite:///%s' % (os.path.join(path_data, 'db', '{package_name}.db'.format(package_name=P.package_name)))
        from framework.util import Util
        Util.save_from_dict_to_json(P.plugin_info, os.path.join(os.path.dirname(__file__), 'info.json'))

        from .gdsmanager import GdsManager

        P.module_list = [GdsManager(P)]
        P.logic = Logic(P)
        default_route(P)

    except Exception as e: 
        P.logger.error('Exception:%s', e)
        P.logger.error(traceback.format_exc())

@P.blueprint.route('/api/<sub>', methods=['GET', 'POST'])
def baseapi(sub):
    P.logger.debug('API: %s', sub)
    try:
        from .gdsmanager import GdsManager
        if sub == 'scan_completed':
            GdsManager.callback_handler(request.form)
            return 'ok'
        elif sub == 'proxy':
            pass

    except Exception as e: 
        P.logger.error('Exception:%s', e)
        P.logger.error(traceback.format_exc())

logger = P.logger
initialize()
