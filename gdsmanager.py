#########################################################
# python
import os, sys, traceback, re, json, threading, time
from datetime import datetime, timedelta
# third-party
import requests
from flask import request, render_template, jsonify, redirect, Response
# sjva
from framework import py_urllib, py_urllib2, SystemModelSetting, path_data, scheduler, db, socketio, py_queue
#from framework.common.util import convert_srt_to_vtt as convSrt2Vtt # TODO
from plugin import LogicModuleBase
from tool_base import ToolUtil 
from system.logic_command import SystemLogicCommand

from lib_gdrive import LibGdrive
from tool_base import ToolRclone, ToolBaseNotify
from plex.model import ModelSetting as PlexModelSetting
from plex.logic_normal import LogicNormal as PlexLogicNormal
from plex.logic import Logic as PlexLogic

from .models import ModelWatchTargetItem as WatchItem
from .models import ModelScanItem as ScanItem

#########################################################
from .plugin import P
logger = P.logger
package_name = P.package_name
ModelSetting = P.ModelSetting

class GdsManager(LogicModuleBase):
    db_default = {
        'db_version': '2',

        # for schedule
        'base_auto_start': 'False',
        'base_interval': '30',

        # for cache
        'gds_dir_cache':'{}',
        'gds_last_remote':'',
        'gds_last_folderid':'',
        'gds_last_path':'',

        # for GDS관리
        'gds_remote_name': 'GDS',
        'gds_plex_mount_path': '/mnt/gds',
        'gds_rc_addr': '127.0.0.1:5572',
        'gds_use_rc_auth': 'False',
        'gds_rc_user': 'sjva',
        'gds_rc_pass': 'sjva',
        'query_parents_limit':'50',

        # etc
        'gds_chunk_size': '1048756',
        'gds_dir_cache_limit':'1000',
        'scan_notify': 'False',
        'daily_full_scan': 'False',
        'fullscan_interval': '0',
        'execute_delta_min': '0',
        'except_paths': '',
    }


    def __init__(self, P):
        super(GdsManager, self).__init__(P, 'browser')
        self.name = 'base'
        self.test = None
        self.dir_cache = {}
        self.last_remote = ''
        self.last_folderid = ''
        self.last_path = ''
        self.dir_cache = {}
        self.token_cache = {}
        self.fullscan_interval = 0
        
        # for gds
        self.gds_auth_status = False
        self.gds_sa_info = None
        self.gds_impersonate = None
        self.gds_root_folder_id = None
        self.gds_scopes = None
        self.gds_creds = None
        self.except_paths = []

        self.FullScanQueue = None
        self.FullScanThread = None

    def plugin_load(self):
        self.dir_cache = json.loads(ModelSetting.get('gds_dir_cache'))
        logger.debug('load dircache: '+str(len(self.dir_cache))+ ' item(s) loaded')
        self.last_remote = ModelSetting.get('gds_last_remote')
        self.last_folderid = ModelSetting.get('gds_last_folderid')
        self.last_path = ModelSetting.get('gds_last_path')
        self.gds_auth_status = self.gds_auth_init()
        self.fullscan_interval = ModelSetting.get_int('fullscan_interval')
        self.except_paths = list(filter(None, sorted(ModelSetting.get_list('except_paths', '\n'))))

        if self.FullScanQueue == None: self.FullScanQueue = py_queue.Queue()
        if self.FullScanThread == None:
            self.FullScanThread = threading.Thread(target=self.fullscan_thread_function, args=())
            self.FullScanThread.daemon = True
            self.FullScanThread.start()

    def plugin_unload(self):
        logger.debug('dump dircache: '+str(len(self.dir_cache))+' item(s) dumped')
        ModelSetting.set('gds_dir_cache', json.dumps(self.dir_cache));
        ModelSetting.set('gds_last_remote', self.last_remote)
        ModelSetting.set('gds_last_folderid', self.last_folderid)
        ModelSetting.set('gds_last_path', self.last_path)

    def setting_save_after(self):
        if self.fullscan_interval != ModelSetting.get_int('fullscan_interval'):
            logger.debug(f'전체스캔 주기 변경: {self.fullscan_interval} -> {ModelSetting.get("fullscan_interval")}')
            self.fullscan_interval = ModelSetting.get_int('fullscan_interval')
            if self.fullscan_interval == 0:
                logger.debug('감시대상 전체스캔을 수행하지 않음')
            else:
                logger.debug(f'감시대상의 전체스캔을 {self.fullscan_interval} 일 후에 실행')

        self.except_paths = list(filter(None, sorted(ModelSetting.get_list('except_paths', '\n'))))

    def migration(self):
        try:
            if ModelSetting.get('db_version') == '1':
                import sqlite3
                db_file = os.path.join(path_data, 'db', '%s.db' % package_name)
                connection = sqlite3.connect(db_file)
                cursor = connection.cursor()
                query = 'ALTER TABLE %s_watch_target_item ADD last_fullscan_time DATETIME' % (package_name)
                cursor.execute(query)
                connection.close()
                ModelSetting.set('db_version', '2')
                db.session.flush()
                logger.debug('last_fullscan_date Alterred')
        except Exception as exception:
            logger.error('Exception:%s', exception)
            logger.error(traceback.format_exc())

    def process_menu(self, sub, req):
        try:
            #logger.debug(f'sub: {sub}')
            #logger.debug(req.form)
            arg = ModelSetting.to_dict()
            name = self.name
            arg['sub'] = name
            arg['proxy_url'] = ToolUtil.make_apikey_url(f'/{package_name}/api/{name}/proxy')
            arg['proxy_subtitle_url'] = ToolUtil.make_apikey_url(f'/{package_name}/api/{name}/proxy')
            #arg['proxy_url'] = ToolUtil.make_apikey_url(f'/system/api/gds')
            #arg['proxy_subtitle_url'] = ToolUtil.make_apikey_url(f'/system/api/gds_subtitle')

            if sub == 'setting':
                arg['scheduler'] = str(scheduler.is_include(self.get_scheduler_name()))
                arg['is_running'] = str(scheduler.is_running(self.get_scheduler_name()))
            elif sub == 'browser':
                arg['remote_names'] = '|'.join(self.get_remote_names())
                # 타겟 폴더 지정하여 로드
                if 'remote_name' in req.form and 'remote_path' in req.form and 'folder_id' in req.form:
                    #logger.debug(f'{req.form["remote_name"]},{req.form["remote_path"]},{req.form["folder_id"]}')
                    arg['last_remote'] = req.form['remote_name']
                    arg['last_folderid'] = req.form['folder_id']
                    arg['last_path'] = req.form['remote_path']
                else:
                    arg['last_remote'] = self.last_remote
                    arg['last_folderid'] = self.last_folderid
                    arg['last_path'] = self.last_path

                arg['gds_remote_name'] = ModelSetting.get('gds_remote_name')
                arg['watch_pathes'] = self.get_watch_pathes()
            elif sub == 'video' or sub == 'vrvideo':
                arg['play_title'] = req.form['play_title']
                arg['play_source_src'] = req.form['play_source_src']
                arg['play_source_type'] = req.form['play_source_type']

                if 'play_subtitle_src' in req.form:
                    arg['play_subtitle_src'] = req.form['play_subtitle_src']
                if sub == 'vrvideo':
                    arg['play_vr_projection'] = req.form['play_vr_projection']
            return render_template(f"{package_name}_{self.name}_{sub}.html", arg=arg)
        except Exception as exception:
            logger.error('Exception:%s', exception)
            logger.error(traceback.format_exc())
            return render_template('sample.html', title=f"{package_name} - {sub}")

    def process_ajax(self, sub, req):
        try:
            #logger.debug(f'AJAX sub: {sub}')
            ret = {'ret':'success'}
            if sub == 'listgdrive':
                ret = self.listgdrive(req)
            elif sub == 'reset_cache':
                self.dir_cache.clear()
                ret['msg'] = '디렉토리 캐시를 초기화하였습니다.'
            elif sub == 'web_list':
                return WatchItem.web_list(req)
            elif sub == 'scan_list':
                return ScanItem.web_list(req)
            elif sub == 'register_watch':
                ret = self.register_watch(req)
            elif sub == 'modify_watch':
                ret = self.modify_watch(req)
            elif sub == 'delete_watch':
                ret = self.delete_watch(req)
            elif sub == 'delete_scan':
                ret = self.delete_scan(req)
            elif sub == 'manual_execute':
                ret = self.manual_execute(req)
            elif sub == 'refresh_vfs':
                ret = self.refresh_vfs(req)
            elif sub == 'send_scan':
                ret = self.send_scan(req)
            elif sub == 'refresh_meta':
                ret = self.refresh_meta(req)
            elif sub == 'one_execute':
                ret = self.one_execute(req)
            elif sub == 'execute_reset':
                ret = self.execute_reset(req)
            elif sub == 'json_load':
                ret = self.json_load(req)
            elif sub == 'gds_auth':
                self.gds_auth_status = self.gds_auth_init()
                if self.gds_auth_status:
                    ret = {'ret':'success', 'msg':'구드공 사용자 인증 성공'}
                else:
                    ret = {'ret':'error', 'msg':'구드공 사용자 인증실패'}

            return jsonify(ret)
        except Exception as e: 
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return jsonify({'ret':'danger', 'msg':str(e)})
 
    def scheduler_function(self):
        self.task()

    def process_api(self, sub, req):
        try:
            #logger.debug(f'sub: {sub}')
            logger.debug(req)

            if sub == 'proxy':
                fileid = request.args.get('f', None)
                remote_name = request.args.get('r', ModelSetting.get('gds_remote_name'))
                kind = request.args.get('k', 'video')
                name = request.args.get('n', None) #file name for subtitle
                logger.info(f"{fileid},{remote_name},{kind},{name}")

                if not fileid:
                    logger.error('fileid is required')
                    return Response('fileid is required', 400, content_type='text/html')

                logger.debug(f'remote_name: {remote_name}')
                token = self.get_access_token_by_remote_name(remote_name, fileid)
                if not token:
                    return Response('Failed to get Token by remote name({remote_name})', 400, content_type='text/html')

                url = f'https://www.googleapis.com/drive/v3/files/{fileid}?alt=media'
                headers = self.get_headers(dict(request.headers), kind, token)
                #logger.debug(headers)
                r = requests.get(url, headers=headers, stream=True)
                if kind == 'subtitle':
                    logger.debug(r.encoding)
                    if r.encoding != None:
                        if r.encoding == 'ISO-8859-1': # 한글자막 인코딩 예외처리
                            try:
                                text = r.content.decode('utf-8', "strict")
                            except Exception as e:
                                logger.error('Exception:%s', e)
                                logger.error(traceback.format_exc())
                                text = r.content.decode('utf-8', "ignore")
                        else:
                            text = r.content.decode(r.encoding, "ignore")
                    else:
                        text = r.text
                    vtt = self.srt2vtt(text)
                    #vtt = convSrt2Vtt(text)
                    r.headers['Content-Type'] = "text/vtt; charset=utf-8"
                    r.headers['Content-Disposition'] = f'inline; filename="subtitle.vtt"'
                    r.headers['Content-Transfer-Encoding'] = 'binary'
                    rv = Response(vtt, r.status_code, content_type=r.headers['Content-Type'])
                    rv.headers.add('Content-Type', r.headers.get('Content-Type'))
                    rv.headers.add('Content-Disposition', r.headers.get('Content-Disposition'))
                    rv.headers.add('Content-Transfer-Encoding', r.headers.get('Content-Transfer-Encoding'))
                    return rv

                chunk = ModelSetting.get_int('gds_chunk_size')
                rv = Response(r.iter_content(chunk_size=int(chunk)), r.status_code, content_type=r.headers['Content-Type'], direct_passthrough=True)
                rv.headers.add('Content-Range', r.headers.get('Content-Range'))
                return rv
            elif sub == 'scan_completed':
                logger.debug(req.form)
                return 'ok'
        except Exception as e: 
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())

    def get_headers(self, headers, kind, token):
        try:
            chunk = ModelSetting.get('gds_chunk_size')
            if kind == "video":
                if 'Range' not in headers or headers['Range'].startswith('bytes=0-'):
                    headers['Range'] = f"bytes=0-{chunk}"
            else: # subtitle
                headers['Accept-Charset'] = 'utf-8, iso-8859-1;q=0.5'
                if 'Range' in headers: del(headers['Range'])
            headers['Authorization'] = f"Bearer {token}"
            headers['Connection'] = 'keep-alive'
            if 'Host' in headers: del(headers['Host'])
            if 'X-Forwarded-Scheme' in headers: del(headers['X-Forwarded-Scheme'])
            if 'X-Forwarded-Proto' in headers: del(headers['X-Forwarded-Proto'])
            if 'X-Forwarded-For' in headers:  del(headers['X-Forwarded-For'])
            if 'Cookie' in headers: del(headers['Cookie'])
            return headers
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def get_remote_names(self):
        remotes = ToolRclone.config_list()
        return [ModelSetting.get('gds_remote_name')] + [x for x in remotes.keys()]

    def get_remote_by_name(self, remote_name):
        try:
            if remote_name == ModelSetting.get('gds_remote_name'):
                return {'root_folder_id':self.gds_root_folder_id, 'service_account_file':ModelSetting.get('gds_sa_path')}
            remotes = ToolRclone.config_list()
            if remote_name in remotes:
                return remotes[remote_name]
            return None
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def gds_auth_init(self):
        try:
            logger.debug('구드공 사용자 인증 시도')
            userid = SystemModelSetting.get('sjva_me_user_id')
            apikey = SystemModelSetting.get('auth_apikey')
            auth_url = "https://sjva.me/sjva/gds_auth.php"
            data = { 'gds_userid':userid, 'gds_apikey':apikey, 'mode':'default' }
            r = requests.post(auth_url, data=data).json()
            if r['result'] != 'success':
                logger.error('구드공 사용자 인증 실패: {}'.format(r['result']))
                return False
            #logger.debug(r['data'])
            self.gds_sa_info = r['data']['remote']['sa']
            self.gds_impersonate = r['data']['remote']['impersonate']
            self.gds_root_folder_id = r['data']['remote']['root_folder_id']
            self.gds_scopes = ['https://www.googleapis.com/auth/{}'.format(r['data']['remote']['scope'])]
            self.gds_creds = LibGdrive.get_credentials_for_gds(self.gds_sa_info, self.gds_scopes, self.gds_impersonate)

            if self.gds_creds == None:
                logger.error('구드공 access token 갱신 오류')
                return False

            logger.debug(f'구드공 사용자 인증 성공: userid({userid}), apikey({apikey})')
            return True

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return False


    def gds_auth(self):
        try:
            if self.gds_creds != None:
                if not self.gds_creds.access_token_expired:
                    return True

            if not self.gds_auth_status:
                self.gds_auth_status = self.gds_auth_init()
                if not self.gds_auth_status:
                    logger.error('구드공 인증 실패')
                    return False

            self.gds_creds = LibGdrive.get_credentials_for_gds(self.gds_sa_info, self.gds_scopes, self.gds_impersonate)
            if self.gds_creds == None:
                logger.error('구드공 access token 갱신 오류')
                return False
            #logger.error('gds token refresh succeed')
            return True

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return False

    def get_access_token_by_remote_name(self, remote_name, fileid):
        try:
            key = f'{remote_name}'
            now = datetime.now()
            # token_cache에 있는 경우
            if key in self.token_cache and now < self.token_cache[key]['time'] + timedelta(minutes=5):
                logger.debug(f'{key} in token_cache: return')
                return self.token_cache[key]['token']

            if remote_name != ModelSetting.get('gds_remote_name'):
                remote = self.get_remote_by_name(remote_name)
                if not remote:
                    logger.error(f'failed to get remote by remote_name({remote_name})')
                    return None

            # 구드공 바로보기 처리
            if remote_name == ModelSetting.get('gds_remote_name'):
                if self.gds_auth() == False:
                    logger.error(f'failed to get authorize by remote_name({remote_name})')
                    return None
                    
                #token = LibGdrive.get_access_token_for_gds(self.gds_sa_info, self.gds_scopes, self.gds_impersonate)
                token = self.gds_creds.access_token
                logger.debug(f'{key}: sa auth for gds')
                self.token_cache[key] = {'token': token, 'time':now}
                return token

            # for user accounts
            if 'token' in remote:
                expiry = datetime.strptime(remote['token']['expiry'].split('.')[0], '%Y-%m-%dT%H:%M:%S')
                if now > expiry:
                    logger.debug('access token expired..')
                    ToolRclone.lsjson(f"{remote_name}:/")
                    return self.get_access_token_by_remote_name(remote_name, fileid)

                logger.debug(f'{key}: user auth')
                return remote['token']['access_token']

            # for service accounts
            try:
                from google.auth.transport.requests import Request as GRequest
                from google.oauth2 import service_account
            except ImportError:
                os.system("{} install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib oauth2client".format(app.config['config']['pip']))

            scopes = ['https://www.googleapis.com/auth/{}'.format(remote['scope'])]
            path_accounts = remote['service_account_file_path']

            import random
            path_sa_json = os.path.join(path_accounts, random.choice(os.listdir(path_accounts)))
            logger.debug(f'selected service-account-json: {path_sa_json}')

            creds = service_account.Credentials.from_service_account_file(path_sa_json, scopes=scopes)
            creds.refresh(GRequest())

            logger.debug(f'{key}: sa auth')
            self.token_cache[key] = {'token': creds.token, 'time':now}
            return creds.token
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def listgdrive(self, req):
        try:
            #logger.debug(req.form)
            ret = {}
            remote_name = req.form['remote_name']
            folder_id = req.form['folder_id']
            path = req.form['path']
            force = req.form['force'] if 'force' in req.form else 'false'
            is_root = False

            #logger.debug(f'listgdrive: {remote_name}:{path},{folder_id},{force}')
            self.last_remote = remote_name
            self.last_folderid = folder_id
            self.last_path = path

            cache_key = remote_name + f':{path}'
            #logger.debug(f'cache_key: {cache_key}')
            if force == 'false' and cache_key in self.dir_cache:
                ret['ret'] = 'success'
                self.dir_cache[cache_key]['count'] = self.dir_cache[cache_key]['count'] + 1
                ret['list'] = self.dir_cache[cache_key]['cache']
                #logger.debug(f'{folder_id} exists in cache.. return')
                return ret

            service = None
            if remote_name == ModelSetting.get('gds_remote_name'):
                #logger.debug('GDS Remote auth by sa info')
                if self.gds_auth() == False:
                    logger.error(f'failed to get authorize by remote_name({remote_name})')
                    return None
                #logger.debug('GDS remote auth success')

            remote = self.get_remote_by_name(remote_name)
            if folder_id == 'root':
                is_root = True
                if 'team_drive' in remote: folder_id = remote['team_drive']
                elif 'root_folder_id' in remote: folder_id = remote['root_folder_id']

            if remote_name == ModelSetting.get('gds_remote_name'):
                service = LibGdrive.sa_authorize_by_info(self.gds_sa_info, scopes=self.gds_scopes, impersonate=self.gds_impersonate, return_service=True)
            else:
                service = LibGdrive.auth_by_rclone_remote(remote)

            if not service:
                logger.error('failed to auth gdrive api')
                return {'ret':'error', 'msg':'failed to auth gdrive api'}

            #logger.debug(f'{folder_id} search gdrive')
            if 'service_account_file' in remote:
                children = LibGdrive.get_children_for_sa(folder_id, service=service, fields=['id','name','mimeType','trashed','size','parents','shortcutDetails'])
            else:
                children = LibGdrive.get_children(folder_id, service=service, fields=['id','name','mimeType','trashed','size','parents','shortcutDetails'])

            if children == None:
                logger.error(f'failed to get children: {folder_id}')
                return {'ret':'error', 'msg':f'failed to children: {folder_id}'}

            ret['ret'] = 'success'
            schildren = sorted(children, key=(lambda x: x['name']))

            parent_id = self.get_parent_id(cache_key, folder_id, service)
            if not is_root:
                pitem = [{'name':'..', 'mimeType':'application/vnd.google-apps.folder', 'id':parent_id, 'trashed':False, 'parents':[], 'size':'-'}]
                schildren = pitem + schildren

            # cache limit over: delete item
            if len(self.dir_cache) == ModelSetting.get_int('gds_dir_cache_limit'):
                del_key = sorted(self.dir_cache, key=lambda x: (self.dir_cache[x]['count']))[0]
                logger.info(f'dir_cache limits over: delete({del_key}) from cache')
                del(self.dir_cache[del_key])

            count = 1
            if cache_key in self.dir_cache:
                if 'count' in self.dir_cache[cache_key]:
                    count = self.dir_cache[cache_key]['count'] + 1

            self.dir_cache[cache_key] = {'cache':schildren, 'count':count}
            ret['list'] = schildren
            return ret
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def register_watch(self, req):
        try:
            logger.debug('add watch target to item')
            logger.debug(req.form)

            remote_path = req.form['remote_path']
            folder_id = req.form['folder_id']
            media_type = req.form['media_type']
            depth = int(req.form['depth'])
            onair = True if req.form['onair'] == 'True' else False
            scheduled = True if req.form['scheduled'] == 'True' else False
            
            entity = None
            entity = WatchItem(remote_path, folder_id, media_type, depth, onair, scheduled)
            if not entity:
                return {'ret':'error', 'msg':f'failed to initialize WatchTargetItem:{remote_path}'}

            logger.debug(f'entity saved: {remote_path},{folder_id},{media_type},{depth},{onair},{scheduled}')
            entity.save()
            return {'ret':'success', 'msg':f'{remote_path}를 감시대상에 등록하였습니다.'}
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def modify_watch(self, req):
        try:
            logger.debug('add watch target to item')
            logger.debug(req.form)

            db_id = int(req.form['id'])
            remote_path = req.form['remote_path']
            folder_id = req.form['folder_id']
            media_type = req.form['media_type']
            depth = int(req.form['depth'])
            onair = True if req.form['onair'] == 'True' else False
            scheduled = True if req.form['scheduled'] == 'True' else False
            
            entity = None
            entity = WatchItem.get_by_id(db_id)
            if not entity:
                return {'ret':'error', 'msg':f'데이터를 찾을 수 없습니다:(ID:{db_id})'}

            # 탐색깊이나 미디어 유형이 바뀐 경우 parents 초기화
            if media_type != entity.media_type or depth != entity.depth:
                entity.parents = None

            entity.media_type = media_type
            entity.depth = depth
            entity.onair = onair
            entity.scheduled = scheduled
            logger.debug(f'entity updated: {db_id},{remote_path},{folder_id},{media_type},{depth},{onair},{scheduled}')
            entity.save()
            return {'ret':'success', 'msg':f'{remote_path} 감시대상정보를 수정하였습니다.'}
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def get_parent_id(self, remote_path, folder_id, service):
        try:
            tmp = remote_path.split('/')
            if tmp[1] == '': return 'root'
            parent_remote = '/'.join(tmp[:-1])
            if parent_remote in self.dir_cache:
                cache = self.dir_cache[parent_remote]['cache']
                parent_id = cache[-1]['parents'][0]
                return parent_id
            ret = LibGdrive.get_file_info(folder_id, service=service)
            if ret['ret'] != 'success': return None
            return ret['data']['parents'][0]

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def delete_watch(self, req):
        try:
            db_id = int(req.form['db_id'])
            entity = None
            entity = WatchItem.get_by_id(db_id)
            if not entity:
                return {'ret':'error', 'msg':f'대상을 찾을 수 없습니다.(ID:{id}'}

            remote_path = entity.remote_path
            logger.debug(f'감시대상 삭제: ID({db_id}), {remote_path}')
            entity.delete(db_id)
            return {'ret':'success', 'msg':f'{remote_path}를 감시대상에서 삭제하였습니다.'}
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def delete_scan(self, req):
        try:
            db_id = int(req.form['db_id'])
            entity = None
            entity = ScanItem.get_by_id(db_id)
            if not entity:
                return {'ret':'error', 'msg':f'대상을 찾을 수 없습니다.(ID:{id}'}

            remote_path = entity.remote_name + ':' + entity.remote_path
            logger.debug(f'스캔내역 삭제: ID({db_id}), {remote_path}')
            entity.delete(db_id)
            return {'ret':'success', 'msg':f'{remote_path}를 스캔목록에서 삭제하였습니다.'}
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def manual_execute(self, req):
        try:
            logger.debug(req.form)
            remote_path = req.form['remote_path']
            folder_id = req.form['folder_id']
            depth = int(req.form['depth'])
            logger.debug(f'manual_execute: {remote_path},{folder_id},{depth}')

            plex_path = self.get_plex_path(remote_path)
            section_id = PlexLogicNormal.get_section_id_by_filepath(plex_path)
            if section_id == -1:
                logger.debug(f'{plex_path} does not exists in Plex Library')
                return {'ret':'error', 'msg':f'Plex 라이브러리에 {plex_path} 를 등록해주세요'}

            if not self.gds_auth():
                return {'ret':'error', 'msg':'인증실패: 다시 시도해주세요'}
            service = LibGdrive.sa_auth_by_creds(self.gds_creds)
            children = LibGdrive.get_children_for_sa(folder_id, service=service, 
                    fields=['id','name','mimeType','trashed','size','parents','shortcutDetails'])

            if children == None:
                logger.error(f'failed to get children: {remote_path}')
                return {'ret':'error', 'msg':f'하위폴더 조회 실패{remote_path}'}

            nchildren = len(children)
            curr = 0; skip = 0; fail = 0; new = 0
            logger.debug(f'{remote_path} has {nchildren} children')

            for child in children:
                curr = curr + 1
                logger.debug(f'child[{curr}/{nchildren}]: started : {child["name"]},{child["id"]}')
                plex_path = self.get_plex_path(remote_path)
                delim = '/' if plex_path[0] == '/' else '\\'
                if child['mimeType'] == 'application/vnd.google-apps.folder' or  \
                        (child['mimeType'] == 'application/vnd.google-apps.shortcut' and \
                                child['shortcutDetails']['targetMimeType'] == 'application/vnd.google-apps.folder'):
                    plex_path = plex_path + delim + child['name']

                logger.debug(f'child[{curr}/{nchildren}]: plex_path: {plex_path}')
                if self.is_in_plex(plex_path):
                    logger.debug(f'child[{curr}/{nchildren}]: SKIP: {plex_path} is already exists in Plex')
                    skip = skip + 1
                    continue

                if not self.is_exists(plex_path):
                    ret = self.gds_vfs_refresh(plex_path)
                    if ret['ret'] != 'success':
                        logger.error(f'child[{curr}/{nchildren}]: failed to vfs/refresh: {plex_path}')
                        fail = fail + 1
                        continue
                else:
                    logger.debug(f'child[{curr}/{nchildren}]: skip refresh - exists in mount path: {plex_path}')

                if not self.plex_send_scan(plex_path, section_id=section_id):
                    logger.error(f'child[{curr}/{nchildren}]: faield to send plex scan: {plex_path}')
                    fail = fail + 1
                    continue
                logger.debug(f'child[{curr}/{nchildren}]: completed {child["name"]},{child["id"]}')
                new = new + 1

            logger.debug(f'manual_execute: END:{remote_path},T:{nchildren},N:{new},S:{skip},F:{fail}')
            return {'ret':'success', 'msg':f'완료: {remote_path}: T:{nchildren},N:{new},S:{skip},F:{fail}'}

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def refresh_vfs(self, req):
        try:
            logger.debug(req.form)
            remote_path = req.form['remote_path']
            folder_id = req.form['folder_id']
            logger.debug(f'refresh_vfs: {remote_path},{folder_id}')

            plex_path = self.get_plex_path(remote_path)
            ret = self.gds_vfs_refresh(plex_path)
            if ret['ret'] != 'success':
                logger.error(f'failed to vfs/refresh: {plex_path}')
                return {'ret':'error', 'msg':f'vfs/refresh 실패:{plex_path}'}

            logger.debug(f'refresh_vfs: END:{remote_path}')
            return {'ret':'success', 'msg':f'완료: {remote_path}'}

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def send_scan(self, req, watch=None):
        try:
            watch_id = -1
            db_id = -1
            if watch != None:
                remote_path = watch.remote_path
                folder_id = watch.folder_id
                watch_id = watch.id
            else:
                logger.debug(req.form)
                remote_path = req.form['remote_path']
                folder_id = req.form['folder_id'] if 'folder_id' in req.form else '-'
                db_id = int(req.form['db_id']) if 'db_id' in req.form else -1

            logger.debug(f'send_scan: {remote_path},{folder_id}')
            scan_list = []

            plex_path = self.get_plex_path(remote_path)
            section_id = PlexLogicNormal.get_section_id_by_filepath(plex_path)
            if section_id == -1:
                found = False
                server_url = PlexModelSetting.get('server_url')
                server_token = PlexModelSetting.get('server_token')
                cmd = 'get_setcion'
                url = '%s/:/plugins/com.plexapp.plugins.SJVA/function/command?cmd=%s&param1=%s&param2=%s&X-Plex-Token=%s' % (server_url, cmd, '', '', server_token)
                #logger.debug(url)
                request = py_urllib2.Request(url)
                response = py_urllib2.urlopen(request)
                data = response.read()
                data = json.loads(data)
                for item in data['data']:
                    if item['location'].startswith(plex_path):
                        found = True
                        scan_list.append({'id':item['id'], 'location':item['location']})
                if found != True:
                    logger.debug(f'{plex_path} does not exists in Plex Library')
                    return {'ret':'error', 'msg':f'Plex 라이브러리에 {plex_path} 를 등록해주세요'}

                tmp = remote_path.split(':', maxsplit=1)
                scan_item = ScanItem(watch_id, tmp[0], tmp[1], folder_id, folder_id, plex_path)
                scan_item.save()
                for s in scan_list:
                    ppath = s['location']
                    sid = int(s['id'])
                    if not self.plex_send_scan(ppath, section_id=sid, callback_id=scan_item.id):
                        logger.error(f'failed to send plex scan: {ppath}({sid})')
                        return {'ret':'error', 'msg':f'plex scan 전송 실패:{ppath}({sid})'}

                scan_item.status = 'scan_sent'
                scan_item.save()
                logger.debug(f'send_scan: END:{remote_path}')
                return {'ret':'success', 'msg':f'완료: {remote_path}'}

            tmp = remote_path.split(':', maxsplit=1)
            if db_id != -1:
                scan_item = ScanItem.get_by_id(db_id)
            else:
                scan_item = ScanItem(-1, tmp[0], tmp[1], folder_id, folder_id, plex_path)
                scan_item.save()

            if not self.plex_send_scan(plex_path, section_id=section_id, callback_id=scan_item.id):
                logger.error(f'failed to send plex scan: {plex_path}')
                return {'ret':'error', 'msg':f'plex scan 전송 실패:{plex_path}'}

            scan_item.status = 'scan_sent'
            scan_item.save()
            logger.debug(f'send_scan: END:{remote_path}')
            return {'ret':'success', 'msg':f'완료: {remote_path}'}

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def get_program_metadata_id(self, meta_id, sub_type=None):
        try:
            for i in range(4):
                query = f'SELECT id,parent_id,metadata_type from metadata_items where id="{meta_id}"'
                ret = PlexLogicNormal.execute_query(query)
                if ret['ret'] != True: return None
                mid, pid, mtype = ret['data'][0].split('|')
                if sub_type != None and sub_type == 'season':
                    if mtype == '3':
                        meta_id = mid
                        break
                if mtype == '2':
                    meta_id = mid
                    break

                meta_id = pid
            target = '시즌' if sub_type == 'season' else '프로그램'
            logger.debug(f'{target} 메타데이터ID: {meta_id}')
            return meta_id

        except Exception as e:
            logger.debug('Exception:%s', e)
            logger.debug(traceback.format_exc())
            return None

    def refresh_meta(self, req):
        try:
            #logger.debug(req.form)
            remote_path = req.form['remote_path']
            folder_id = req.form['folder_id'] if 'folder_id' in req.form else '-'
            db_id = int(req.form['db_id']) if 'db_id' in req.form else -1
            mtype = req.form['mtype']
            target = req.form['target_meta']
            logger.debug(f'메타데이터 갱신요청: {remote_path},{folder_id},{mtype},{target}')

            # 파일을 지정하여 갱신요청한 경우
            if target == 'file' and mtype.startswith('video/'):
                plex_path = self.get_plex_path(remote_path)
                if not PlexLogicNormal.metadata_refresh(filepath=plex_path):
                    logger.error(f'메타데이터 갱신 실패: {plex_path}')
                    return {'ret':'error', 'msg':f'메타데이터 갱신 실패: {remote_path}'}
                logger.debug(f'메타데이터 갱신 요청 완료: {plex_path}')
                return {'ret':'success', 'msg':f'메타데이터 갱신요청 완료: {remote_path}'}

            if mtype.endswith('folder'):
                if remote_path not in self.dir_cache:
                    logger.error(f'메타데이터 갱신 실패: 잘못된 경로/캐시없음({remote_path})')
                    return {'ret':'error', 'msg':f'메타갱신 실패: 잘못된 경로/캐시없음({remote_path})'}
                children = self.dir_cache[remote_path]['cache']
                found = False
                for child in children:
                    if child['name'] == '..': continue
                    if child['mimeType'].startswith('video/'):
                        found = True
                        break

                if not found:
                    logger.error(f'메타데이터 갱신 실패: 잘못된 경로/상위폴더미지원({remote_path})')
                    return {'ret':'error', 'msg':f'메타갱신 실패: 잘못된 경로/상위폴더미지원({remote_path})'}

                remote_path = remote_path + '/' + child['name']

            plex_path = self.get_plex_path(remote_path)
            # 영화의 경우: 부모폴더를 지정하여 메타고침한 케이스
            if target == 'file':
                if not PlexLogicNormal.metadata_refresh(filepath=plex_path):
                    logger.error(f'메타데이터 갱신 실패: 잘못된 경로({plex_path})')
                    return {'ret':'error', 'msg':f'메타데이터 갱신 실패: {remote_path}'}
                logger.debug(f'메타데이터 갱신요청 완료:  {plex_path}')
                return {'ret':'success', 'msg':f'메타데이터 갱신요청 완료: {remote_path}'}

            logger.debug(f'메타조회 시도: {remote_path},{plex_path}')
            # 쇼의 경우
            try:
                metadata_id = PlexLogicNormal.get_library_key_using_bundle(plex_path)
                if metadata_id == None or metadata_id == '':
                    logger.error(f'메타데이터ID 조회 실패: {plex_path}')
                    return {'ret':'error', 'msg':f'메타갱신 실패: 메타데이터ID 획득 실패({remote_path})'}
            except:
                logger.error(f'메타데이터ID 조회 실패: {plex_path}')
                return {'ret':'error', 'msg':f'메타갱신 실패: 메타데이터ID를 획득 실패({remote_path})'}

            sub_type = 'season' if target == 'season' else None
            target_metadata_id = self.get_program_metadata_id(metadata_id, sub_type=sub_type)
            if target_metadata_id == None:
                logger.error(f'{target.upper()} 메타데이터ID 조회 실패: {plex_path}')
                return {'ret':'error', 'msg':f'메타갱신 실패: 메타데이터ID를 획득 실패({remote_path})'}

            logger.debug(f'메타데이터 갱신요청: {plex_path},{target.upper()},{target_metadata_id}')
            if not PlexLogicNormal.metadata_refresh(metadata_id=target_metadata_id):
                logger.error(f'메타데이터 갱신실패: {plex_path},{target.upper()},{target_metadata_id}')
                return {'ret':'error', 'msg':f'메타데이터 갱신 실패: {remote_path}'}
            logger.debug(f'메타데이터 갱신요청 완료: {plex_path},{target.upper()},{target_metadata_id}')
            return {'ret':'success', 'msg':f'메타데이터 갱신요청 완료: {remote_path}'}
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def srt2vtt(self, srt):
        try:
            logger.debug('convert srt to vtt')
            vtt = 'WEBVTT\n\n'
            lines = srt.splitlines()
            for line in lines:
                convline = re.sub(',(?! )', '.', line)
                vtt = vtt + convline + '\n'
            #logger.debug(vtt)
            return vtt
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def load_subfolders(self, entity, service):
        try:
            parents = {}
            logger.debug('load_subfolders')
            remote_path = entity.remote_path
            folders = [{'remote_path':remote_path, 'folder_id':entity.folder_id}]
            if entity.depth == 1: parents[entity.folder_id] = remote_path
            # 방송중인 show 의 경우 원래depth까지 탐색 대상에 포함
            target_depth = entity.depth if (entity.media_type == 'show' and entity.onair) else entity.depth-1
            for i in range(0, target_depth):
                sub_folders = []
                for f in folders:
                    children = LibGdrive.get_children_for_sa(f['folder_id'], service=service, 
                            fields=['id','name','mimeType','trashed','size','parents','shortcutDetails'])
                    remote_path = f['remote_path']
                    for child in children:
                        if child['mimeType'] == 'application/vnd.google-apps.folder' or \
                                (child['mimeType'] == 'application/vnd.google-apps.shortcut' and \
                                child['shortcutDetails']['targetMimeType'] == 'application/vnd.google-apps.folder'):
                            sub_remote_path = os.path.join(remote_path, child['name'])
                            folder_id = child['id'] if child['mimeType'].endswith('apps.folder') else child['shortcutDetails']['targetId']
                            parents[folder_id] = sub_remote_path
                            sub_folders.append({'remote_path':sub_remote_path, 'folder_id':folder_id})
                            #logger.debug(f'add target folder: {sub_remote_path}, {folder_id}')

                folders = sub_folders[:]
            return parents
                
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def task(self):
        try:
            logger.debug('스케쥴러 시작')
            entities = WatchItem.get_scheduled_entities()
            total = len(entities)
            logger.debug('감시대상 폴더: %d', total)
            if total != 0 and self.gds_auth():
                service = LibGdrive.sa_auth_by_creds(self.gds_creds)
                if service == None:
                    logger.error('Gdrive API 인증 실패, sjva.me 사용자ID, apikey를 확인해주세요')
                    return

                for entity in entities:
                    # 감시대상 하위폴더 로딩
                    if entity.subfolders == None:
                        subfolders = self.load_subfolders(entity, service)
                        if subfolders == None:
                            logger.error(f'감시대상 하위폴더 로딩 실패: {entity.remote_path}')
                            continue
                        entity.subfolders = json.dumps(subfolders)
                        entity.save()
                    # 이미 하위폴더를 로드한 경우 db에서 불러옴
                    else:
                        subfolders = json.loads(entity.subfolders)

                    now = datetime.now()
                    if entity.last_fullscan_time == None:
                        entity.last_fullscan_time = entity.last_updated_time if entity.last_updated_time != None else entity.created_time

                    if self.is_fullscan_target(entity.last_fullscan_time):
                        logger.debug(f'감시대상 전체스캔: {entity.remote_path}')

                        def func():
                            self.vfs_refresh_thread(entity.id)

                        thread = threading.Thread(target=func, args=())
                        thread.setDaemon(True)
                        thread.start()

                        entity.last_updated_time = now
                        entity.last_fullscan_time = now
                        entity.save()
                        continue

                    target_parents = [x for x in subfolders.keys()]
                    #logger.debug(f'target_parents: {target_parents}')
                    target_time = entity.last_updated_time if entity.last_updated_time != None else entity.created_time
                    logger.debug(f'감시대상 폴더 스캔: {entity.remote_path}')
                    children = LibGdrive.get_children2(target_parents, mtypes=['video/', 'folder', 'shortcut'],service=service, 
                            time_after=target_time, fields=['id','name','mimeType','trashed','size','parents','shortcutDetails'], 
                            order_by='createdTime', limit=ModelSetting.get_int('query_parents_limit'))
                    if children == None:
                        logger.error(f'자식폴더 조회 실패: {entity.remote_path}')
                        continue

                    nchildren = len(children)
                    logger.debug(f'대상경로({entity.remote_path})에  {nchildren}개의 새로운 파일/폴더 검색됨')
                    if nchildren == 0:
                        entity.last_updated_time = now
                        entity.save()

                    curr = 0
                    for child in children:
                        curr = curr + 1
                        logger.debug(f'처리[{curr}/{nchildren}]: {child["name"]},{child["id"]},{child["mimeType"]},{child["parents"][0]}')
                        parent_id = child['parents'][0]
                        mtype = 'folder'
                        # 폴더인 경우
                        if child['mimeType'].endswith('folder') or \
                                (child['mimeType'].endswith('shortcut') and \
                                child['shortcutDetails']['targetMimeType'].endswith('folder')):
                            sub_remote_path = subfolders[parent_id] + '/' + child['name']

                            # 방영중 SHOW 감시대상에 없던 새로운 subfolder가 추가된 경우 감시폴더에 추가
                            if entity.media_type == 'show' and entity.onair:
                                logger.debug(f'처리[{curr}/{nchildren}]: 감시폴더에 신규대상 추가({sub_remote_path})')
                                subfolders[child['id']] = sub_remote_path
                        # 영상인 경우
                        else:
                            mtype = 'video'
                            sub_remote_path = subfolders[parent_id] + '/' + child['name']

                        # 제외폴더의 경우 스킵
                        if self.is_except_path(sub_remote_path.split(':', maxsplit=1)[1]):
                            logger.debug(f'처리[{curr}/{nchildren}]: SKIP: 제외대상 경로에 포함({sub_remote_path})')
                            continue

                        # Plex에 있는 경우 스킵
                        plex_path = self.get_plex_path(sub_remote_path)
                        logger.debug(f'처리[{curr}/{nchildren}]: 리모트({sub_remote_path}), PLEX({plex_path})')
                        if self.is_in_plex(plex_path, mtype=mtype):
                            logger.debug(f'처리[{curr}/{nchildren}]: SKIP: Plex에 존재하는 파일({plex_path})')
                            continue

                        delim = '/' if plex_path[0] == '/' else '\\'
                        ppath = delim.join(plex_path.split(delim)[:-1])
                        if mtype == 'folder':
                            if not PlexLogicNormal.os_path_exists(plex_path):
                                ret = self.gds_vfs_refresh(ppath, _async=False)
                                if ret['ret'] != 'success':
                                    logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({ppath})')
                                    continue
                                ret = self.gds_vfs_refresh(plex_path)
                                if ret['ret'] != 'success':
                                    logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({plex_path})')
                                    continue
                        else:
                            if not PlexLogicNormal.os_path_exists(ppath):
                                gppath = delim.join(plex_path.split(delim)[:-2])
                                ret = self.gds_vfs_refresh(gppath, _async=False)
                                if ret['ret'] != 'success':
                                    logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({gppath})')
                                    continue
                                ret = self.gds_vfs_refresh(ppath)
                                if ret['ret'] != 'success':
                                    logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({ppath})')
                                    continue
                            else:
                                if not PlexLogicNormal.os_path_exists(plex_path):
                                    ret = self.gds_vfs_refresh(ppath)
                                    if ret['ret'] != 'success':
                                        logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({ppath})')
                                        continue

                        rname, rpath = sub_remote_path.split(':', maxsplit=1)
                        scan_folder_id = parent_id if mtype == 'video' else child['id']
                        scan_item = ScanItem.get_by_folder_id(scan_folder_id)
                        if scan_item == None:
                            scan_item = ScanItem(entity.id, rname, rpath, scan_folder_id, parent_id, plex_path)
                            scan_item.save()

                        # plex send scan
                        if not self.plex_send_scan(plex_path, callback_id=scan_item.id):
                            logger.error(f'처리[{curr}/{nchildren}]: 스캔명령 전송 실패({plex_path})')
                            continue

                        scan_item.status = 'scan_sent'
                        scan_item.updated_time = now
                        scan_item.save()

                        logger.debug(f'처리[{curr}/{nchildren}]: 스캔명령 전송 완료({plex_path})')

                    entity.subfolders = json.dumps(subfolders)
                    entity.last_updated_time = now
                    entity.save()
            
            logger.debug('스케쥴러 완료')
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def is_in_plex(self, plex_path, mtype='folder'):
        try:
            ret = PlexLogicNormal.find_by_filename_part(plex_path)
            #logger.debug(ret)
            if not ret['ret']: return False
            for item in ret['list']:
                if mtype == 'folder':
                    if item['dir'].startswith(plex_path): return True
                else:
                    if plex_path.find(item['filename']) != -1: return True
            return False

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return False

    def is_exists(self, plex_path):
         return PlexLogicNormal.os_path_exists(plex_path)

    def get_plex_path(self, remote_path):
        try:
            ret = remote_path
            gds_remote_root = ModelSetting.get('gds_remote_name') + ':'
            plex_mount_path = ModelSetting.get('gds_plex_mount_path')
            if remote_path.startswith(gds_remote_root):
                ret = remote_path.replace(gds_remote_root, plex_mount_path)
                if plex_mount_path[0] != '/': ret = ret.replace('/', '\\')
            return ret

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def get_remote_path(self, plex_path):
        try:
            ret = plex_path
            gds_remote_root = ModelSetting.get('gds_remote_name') + ':'
            plex_mount_path = ModelSetting.get('gds_plex_mount_path')
            if plex_path.startswith(plex_mount_path):
                ret = plex_mount_path.replace(plex_mount_path, gds_remote_root).replace('\\\\', '\\').replace('\\', '/')
                if plex_mount_path[0] != '/': ret = ret.replace('\\', '/')
            return ret

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def get_watch_pathes(self):
        pathes = list(set([x.remote_path for x in WatchItem.get_all_entities()]))
        return '|'.join(pathes)

    def get_rc_path(self, plex_path):
        try:
            ret = plex_path
            plex_mount_path = ModelSetting.get('gds_plex_mount_path')
            ret = ret.replace(plex_mount_path, '').replace('\\\\','\\').replace('\\','/')
            if ret[0] == '/': ret = ret[1:]
            return ret

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return None

    def gds_vfs_refresh(self, plex_path, _async=True, recursive=False):
        try:
            rc_path = self.get_rc_path(plex_path)
            logger.debug(f'rc_path: {rc_path}')
            command = ['rclone', 'rc', 'vfs/refresh', '--rc-addr']
            command.append(ModelSetting.get('gds_rc_addr'))
            if ModelSetting.get_bool('gds_use_rc_auth'):
                command.append('--rc-user')
                command.append(ModelSetting.get('gds_rc_user'))
                command.append('--rc-pass')
                command.append(ModelSetting.get('gds_rc_pass'))

            command.append(f'dir={rc_path}')
            if _async: command.append('_async=true')
            if recursive:
                command.append('recursive=true')
                command.append('--fast-list')
            logger.debug(command)
            ret = SystemLogicCommand.execute_command_return(command, format='json')
            logger.debug(ret)
            if ret == None:
                return {'ret':'error', 'msg':f'마운트 경로({rc_path}) 갱신이 실패하였습니다.(mount rc확인필요)'}

            if _async: # async
                if 'jobid' not in ret:
                    return {'ret':'error', 'msg':f'마운트 경로({rc_path}) 갱신이 실패하였습니다.(mount rc확인필요)'}
                return {'ret':'success', 'msg':f'VFS/REFRESH 요청완료({ret["jobid"]}:{rc_path})', 'jobid':ret['jobid']}

            # direct
            if ret['result'][rc_path] != 'OK':
                return {'ret':'error', 'msg':f'마운트 경로({rc_path}) 갱신이 실패하였습니다.(mount rc확인필요)'}
            return {'ret':'success', 'msg':f'VFS/REFRESH 요청완료({rc_path})', 'jobid':ret['jobid']}

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())

    def execute_reset(self, req):
        try:
            target = req.form['target']
            str_target = '감시대상목록' if target == 'watch' else '스캔내역목록'
            logger.debug(f'모든 항목 삭제: 삭제대상({str_target})')
            if target == 'watch':
                db.session.query(WatchItem).delete()
                db.session.commit()
            else:
                db.session.query(ScanItem).delete()
                db.session.commit()
            return {'ret':'success', 'msg':f'모든 {str_target} 을 삭제하였습니다.'}

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':f'삭제 실패: 로그를 확인해주세요.'}
 
    def one_execute(self, req):
        try:
            db_id = int(req.form['db_id'])
            entity = WatchItem.get_by_id(db_id)
            if entity == None:
                return {'ret':'error', 'msg':f'잘못된 id입니다.{db_id}'}

            logger.debug(f'1회실행: {entity.remote_path},{entity.folder_id},{entity.depth}')
            if not self.gds_auth():
                return {'ret':'error', 'msg':'인증실패: 잠시 후 다시 시도해주세요'}

            now = datetime.now()
            service = LibGdrive.sa_auth_by_creds(self.gds_creds)
            target_time = entity.last_updated_time if entity.last_updated_time != None else entity.created_time
            delta_min = ModelSetting.get_int('execute_delta_min')
            if delta_min > 0: target_time = target_time - timedelta(minutes=delta_min)

            # 감시대상 하위폴더 로딩
            if entity.subfolders == None:
                subfolders = self.load_subfolders(entity, service)
                if subfolders == None:
                    logger.error(f'감시대상 하위폴더 로딩 실패: {entity.remote_path}')
                    return {'ret':'error', 'msg':f'감시대상 하위폴더 조회실패: {entity.remote_path}'}
                entity.subfolders = json.dumps(subfolders)
                entity.save()
            # 이미 하위폴더를 로드한 경우 db에서 불러옴
            else:
                subfolders = json.loads(entity.subfolders)

            target_parents = [x for x in subfolders.keys()]
            children = LibGdrive.get_children2(target_parents, mtypes=['video/', 'folder', 'shortcut'],service=service, 
                    time_after=target_time, fields=['id','name','mimeType','trashed','size','parents','shortcutDetails'], 
                    order_by='createdTime', limit=ModelSetting.get_int('query_parents_limit'))
            if children == None:
                logger.error(f'자식폴더 변경사항 조회 실패: {entity.remote_path}')
                return {'ret':'error', 'msg':f'변경사항 조회 실패{entity.remote_path}'}

            remote_path = entity.remote_path
            folder_id = entity.folder_id
            nchildren = len(children)
            logger.debug(f'대상경로({entity.remote_path})에  {nchildren}개의 새로운 파일/폴더 검색됨')
            if nchildren == 0:
                entity.last_updated_time = now
                entity.save()
                return {'ret':'success', 'msg':f'완료: {entity.remote_path}, 새로운 파일/폴더가 없습니다.'}

            curr = 0
            for child in children:
                curr = curr + 1
                logger.debug(f'처리[{curr}/{nchildren}]: {child["name"]},{child["id"]},{child["mimeType"]},{child["parents"][0]}')
                parent_id = child['parents'][0]
                mtype = 'folder'

                # 폴더인 경우
                if child['mimeType'].endswith('folder') or \
                        (child['mimeType'].endswith('shortcut') and \
                        child['shortcutDetails']['targetMimeType'].endswith('folder')):
                    sub_remote_path = subfolders[parent_id] + '/' + child['name']

                    # 방영중 SHOW 감시대상에 없던 새로운 subfolder가 추가된 경우 감시폴더에 추가
                    if entity.media_type == 'show' and entity.onair:
                        logger.debug(f'처리[{curr}/{nchildren}]: 감시폴더에 신규대상 추가({sub_remote_path})')
                        subfolders[child['id']] = sub_remote_path
                # 영상인 경우
                else:
                    mtype = 'video'
                    sub_remote_path = subfolders[parent_id] + '/' + child['name']

                if self.is_except_path(sub_remote_path.split(':', maxsplit=1)[1]):
                    logger.debug(f'처리[{curr}/{nchildren}]: SKIP: 제외대상 경로에 포함({sub_remote_path})')
                    continue

                # Plex에 있는 경우 스킵
                plex_path = self.get_plex_path(sub_remote_path)
                logger.debug(f'처리[{curr}/{nchildren}]: 리모트({sub_remote_path}), PLEX({plex_path})')
                if self.is_in_plex(plex_path, mtype=mtype):
                    logger.debug(f'처리[{curr}/{nchildren}]: SKIP: Plex에 존재하는 파일({plex_path})')
                    continue

                # 마운트 캐시 확인 및 갱신
                delim = '/' if plex_path[0] == '/' else '\\'
                ppath = delim.join(plex_path.split(delim)[:-1])
                if mtype == 'folder':
                    if not PlexLogicNormal.os_path_exists(plex_path):
                        ret = self.gds_vfs_refresh(ppath, _async=False)
                        if ret['ret'] != 'success':
                            logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({ppath})')
                            continue
                        logger.debug(f'12312312pp:{plex_path}')
                        ret = self.gds_vfs_refresh(plex_path)
                        if ret['ret'] != 'success':
                            logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({plex_path})')
                            continue
                else:
                    if not PlexLogicNormal.os_path_exists(ppath):
                        gppath = delim.join(plex_path.split(delim)[:-2])
                        ret = self.gds_vfs_refresh(gppath, _async=False)
                        if ret['ret'] != 'success':
                            logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({gppath})')
                            continue
                        ret = self.gds_vfs_refresh(ppath)
                        if ret['ret'] != 'success':
                            logger.error(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 실패({ppath})')
                            continue

                #logger.debug(f'처리[{curr}/{nchildren}]: 마운트캐시 갱신 완료({plex_path})')
                rname, rpath = sub_remote_path.split(':', maxsplit=1)
                scan_folder_id = parent_id if mtype == 'video' else child['id']
                scan_item = ScanItem.get_by_folder_id(scan_folder_id)
                if scan_item == None:
                    scan_item = ScanItem(entity.id, rname, rpath, scan_folder_id, parent_id, plex_path)

                # plex send scan
                if not self.plex_send_scan(plex_path, callback_id=scan_item.id):
                    logger.error(f'처리[{curr}/{nchildren}]: 스캔명령 전송 실패({plex_path})')
                    continue

                scan_item.status = 'scan_sent'
                scan_item.updated_time = now
                scan_item.save()

                logger.debug(f'처리[{curr}/{nchildren}]: 스캔명령 전송 완료({plex_path})')

            entity.subfolders = json.dumps(subfolders)
            entity.last_updated_time = now
            entity.save()

            return {'ret':'success', 'msg':f'완료: {entity.remote_path}'}
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())

    def plex_send_scan(self, plex_path, callback_id=None, section_id = -1):
        try:
            logger.debug(f'스캔명령 전송: {plex_path},{callback_id}')
            if section_id == -1:
                section_id = PlexLogicNormal.get_section_id_by_filepath(plex_path)
                if section_id == -1:
                    logger.error(f'failed to get section_id by path: {plex_path}')
                    return False

            scan_path = py_urllib.quote(plex_path)
            ddns = SystemModelSetting.get('ddns')
            apikey = SystemModelSetting.get('auth_apikey')
            ret = PlexLogic.send_scan_command2(package_name, section_id, scan_path, str(callback_id), 'ADD', package_name)
            r = json.loads(ret.decode('utf-8')) if type(ret) == bytes else json.loads(ret)
            if r['ret'] == 'ADD_OK': return True
            else: return False
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())

    def json_load(self, req):
        try:
            logger.debug(req.form)
            fileid = req.form['fileid']
            remote_name = req.form['remote_name']
            if not fileid:
                logger.error('fileid is required')
                return {'ret':'error', 'msg':'fileid is required'}

            if self.gds_auth() == False:
                logger.error(f'failed to get authorize by remote_name({remote_name})')
                return {'ret':'error', 'msg': 'Failed to auth gdrive api'}

            service = LibGdrive.sa_auth_by_creds(self.gds_creds)
            if not service:
                return {'ret':'error', 'msg': 'Failed to auth gdrive api'}

            import io
            from apiclient.http import MediaIoBaseDownload
            request = service.files().get_media(fileId=fileid)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False: status, done = downloader.next_chunk()
            json_data = json.loads(fh.getvalue().decode('utf-8'))
            return {'ret':'success', 'data':json_data}

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
            return {'ret':'error', 'msg':str(e)}

    def str2date(self, time_str):
        fm = '%Y-%m-%d %H:%M:%S'
        return datetime.strptime(time_str, fm)

    def date2str(self, time):
        fm = '%Y-%m-%d %H:%M:%S'
        return time.strftime(fm)

    def is_fullscan_target(self, prev_fullscan_time):
        interval = ModelSetting.get_int('fullscan_interval')
        if interval == 0: return False
        now = datetime.now()
        target_time = prev_fullscan_time + timedelta(days=interval)
        return (now > target_time)

    def is_except_path(self, path):
        from fnmatch import fnmatch
        for ex in self.except_paths:
            if fnmatch(path, ex):
                return True
        return False

    def check_rc_job_completed(self, jobid):
        try:
            count = 0
            command = ['rclone', 'rc', '--json']
            job = {"jobid":f'{jobid}'}
            command.append(json.dumps(job))
            command.append('job/status')
            command.append('--rc-addr')
            command.append(ModelSetting.get('gds_rc_addr'))
            if ModelSetting.get_bool('gds_use_rc_auth'):
                command.append('--rc-user')
                command.append(ModelSetting.get('gds_rc_user'))
                command.append('--rc-pass')
                command.append(ModelSetting.get('gds_rc_pass'))

            while True:
                time.sleep(10)
                ret = SystemLogicCommand.execute_command_return(command, format='json')
                logger.debug(ret)
                if ret['finished'] or count > 30:
                    break
                logger.debug(f'jobid({jobid}) 작업 진행중(1분후 재확인)')
                time.sleep(50)
                count = count + 1

            return ret['success']
        except Exception as e:
            logger.debug('Exception:%s', e)
            logger.debug(traceback.format_exc())
            return False


    def vfs_refresh_thread(self, db_id):
        try:
            logger.debug(f'[전체스캔] rclone 캐시 갱신: id({db_id})')
            entity = WatchItem.get_by_id(db_id)
            plex_path = self.get_plex_path(entity.remote_path)
            ret = self.gds_vfs_refresh(plex_path, _async=True, recursive=True)
            if ret['ret'] != 'success':
                logger.error(f'[전체스캔] vfs/refresh 전송 실패:{plex_path}')
                self.FullScanQueue.task_done()
                return

            jobid = ret['jobid']
            logger.debug(f'[전체스캔] rclone 캐시 갱신요청 완료:{plex_path},{jobid}')
            success = self.check_rc_job_completed(jobid)
            if success:
                logger.debug(f'[전체스캔] rclone 캐시 갱신처리 완료: {plex_path}')
                req = {'id': db_id}
                self.FullScanQueue.put(req)
            else:
                logger.debug(f'[전체스캔] rclone 캐시 갱신처리 실패: {plex_path})')

        except Exception as e:
            logger.debug('Exception:%s', e)
            logger.debug(traceback.format_exc())
            return

    def fullscan_thread_function(self):
        logger.debug('[전체스캔] 전체경로 스캔 쓰레드 시작')
        while True:
            try:
                req = self.FullScanQueue.get()
                logger.debug(req)
                db_id = req['id']
                entity = WatchItem.get_by_id(db_id)

                remote_path = entity.remote_path
                folder_id = entity.folder_id
                plex_path = self.get_plex_path(remote_path)

                scan_list = []
                logger.debug(f'[전체스캔] 스캔 전송 시작: {plex_path}')
                section_id = PlexLogicNormal.get_section_id_by_filepath(plex_path)
                if section_id == -1:
                    found = False
                    server_url = PlexModelSetting.get('server_url')
                    server_token = PlexModelSetting.get('server_token')
                    cmd = 'get_setcion'
                    url = '%s/:/plugins/com.plexapp.plugins.SJVA/function/command?cmd=%s&param1=%s&param2=%s&X-Plex-Token=%s' % (server_url, cmd, '', '', server_token)
                    #logger.debug(url)
                    request = py_urllib2.Request(url)
                    response = py_urllib2.urlopen(request)
                    data = response.read()
                    data = json.loads(data)
                    for item in data['data']:
                        if item['location'].startswith(plex_path):
                            found = True
                            scan_list.append({'id':item['id'], 'location':item['location']})

                    if not found:
                        logger.error('[전체스캔] Plex에 경로를 추가해주세요({plex_path})')
                        self.FullScanQueue.task_done()
                        continue

                    tmp = remote_path.split(':', maxsplit=1)
                    scan_item = ScanItem(entity.id, tmp[0], tmp[1], folder_id, folder_id, plex_path)
                    scan_item.save()

                    for s in scan_list:
                        lpath = s['location']
                        sid = int(s['id'])
                        if not self.plex_send_scan(lpath, section_id=sid, callback_id=scan_item.id):
                            logger.error(f'[전체스캔] 스캔명령 전송 실패: {lpath}({sid})')
                            continue
                else:
                    tmp = remote_path.split(':', maxsplit=1)
                    scan_item = ScanItem(entity.id, tmp[0], tmp[1], entity.folder_id, entity.folder_id, plex_path)
                    scan_item.save()
                    if not self.plex_send_scan(plex_path, section_id=section_id, callback_id=scan_item.id):
                        logger.error(f'[전체스캔] 스캔명령 전송 실패: {lpath}({sid})')
                        continue
    
                scan_item.status = 'scan_sent'
                scan_item.save()
                self.FullScanQueue.task_done()
                logger.debug(f'[전체스캔] 스캔 전송 완료: {plex_path}')

            except Exception as e:
                logger.debug('Exception:%s', e)
                logger.debug(traceback.format_exc())

    @staticmethod
    def send_noti(scan_id, filename=None):
        message_id = 'gdsmanager_scan_completed'
        e = ScanItem.get_by_id(scan_id)
        if not e: return
        if filename != None:
            if filename == e.plex_path:
                msg = '[구드공관리] 신규아이템 추가 알림\n'
            else:
                msg = '[구드공관리] 스캔완료 알림\n'
            msg = msg + f'경로: {filename}\n'
        else:
            msg = '[구드공관리] 신규아이템 추가 알림\n'
            msg = msg + f'경로: {e.remote_path}\n'
        ToolBaseNotify.send_message(msg, message_id=message_id)

    @staticmethod
    def callback_handler(req):
        try:
            callback_id = req['id'] if 'id' in req else None
            filename = req['filename']
            logger.debug(f'스캔완료: callback_id({callback_id}), filename({filename})')
            scan_item = None
            try:
                scan_id = int(callback_id)
                scan_item = ScanItem.get_by_id(scan_id)
            except ValueError:
                scan_item = ScanItem.get_by_plex_path(filename)

            if not scan_item: return;
            scan_item.status = 'completed'
            scan_item.updated_time = datetime.now()
            scan_item.save()
            if ModelSetting.get_bool('scan_notify'):
                GdsManager.send_noti(scan_item.id, filename=filename)

        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())
