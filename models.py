import os, sys, traceback, re, json, threading, time, shutil
from framework import db, py_unicode
from framework.util import Util
from sqlalchemy import or_, and_, func, not_, desc
from datetime import datetime
from .plugin import P

logger = P.logger
package_name = P.package_name
ModelSetting = P.ModelSetting

class ModelWatchTargetItem(db.Model):
    __tablename__ = '%s_watch_target_item' % (package_name)
    __table_args__ = {'mysql_collate': 'utf8_general_ci'}
    __bind_key__ = package_name

    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    reserved = db.Column(db.JSON)
    remote_path = db.Column(db.String)
    folder_id = db.Column(db.String)
    depth = db.Column(db.Integer)
    media_type = db.Column(db.String)
    onair = db.Column(db.Boolean)
    scheduled = db.Column(db.Boolean)
    subfolders = db.Column(db.JSON)
    last_updated_time = db.Column(db.DateTime)
    last_fullscan_time = db.Column(db.DateTime)

    def __init__(self, remote_path, folder_id, media_type, depth, onair, scheduled):
        self.created_time = datetime.now()
        self.remote_path = py_unicode(remote_path)
        self.folder_id = py_unicode(folder_id)
        self.depth = depth
        self.media_type = py_unicode(media_type)
        self.onair = onair
        self.scheduled = scheduled
        self.subfolders = None
        self.last_updated_time = None
        self.last_fullscan_time = None

    def __repr__(self):
        return repr(self.as_dict())

    def as_dict(self):
        ret = {x.name: getattr(self, x.name) for x in self.__table__.columns}
        ret['created_time'] = self.created_time.strftime('%m-%d %H:%M:%S') 
        if self.last_updated_time == None: ret['last_updated_time'] = u'-'
        else: ret['last_updated_time'] = self.last_updated_time.strftime('%m-%d %H:%M:%S') 
        if self.last_fullscan_time == None: ret['last_fullscan_time'] = u'-'
        else: ret['last_fullscan_time'] = self.last_fullscan_time.strftime('%m-%d %H:%M:%S') 
        return ret

    def save(self):
        db.session.add(self)
        db.session.commit()

    @classmethod
    def delete(cls, id):
        db.session.query(cls).filter_by(id=id).delete()
        db.session.commit()

    @classmethod
    def get_by_id(cls, id):
        return db.session.query(cls).filter_by(id=id).first()
    
    @classmethod
    def get_by_remote_path(cls, remote_path):
        return db.session.query(cls).filter_by(remote_path=remote_path).first()

    @classmethod
    def get_by_folder_id(cls, folder_id):
        return db.session.query(cls).filter_by(folder_id=folder_id).first()

    @classmethod
    def get_all_entities(cls):
        return db.session.query(cls).all()

    @classmethod
    def get_scheduled_entities(cls):
        query = db.session.query(cls)
        return query.filter(cls.scheduled==True).all()

    @classmethod
    def get_all_entities(cls):
        return db.session.query(cls).all()

    @classmethod
    def web_list(cls, req):
        try:
            ret = {}
            page = 1
            page_size = 30
            search = ''
            order='ctime_desc'
            if 'page' in req.form:
                page = int(req.form['page'])
            if 'search_word' in req.form:
                search = req.form['search_word']
            if 'order' in req.form:
                order = req.form['order']
            query = cls.make_query(search=search, order=order)
            count = query.count()
            query = query.limit(page_size).offset((page-1)*page_size)
            #logger.debug('cls count:%s', count)
            lists = query.all()
            ret['list'] = [item.as_dict() for item in lists]
            ret['paging'] = Util.get_paging_info(count, page, page_size)
            return ret
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())


    @classmethod
    def make_query(cls, search='', order='ctime_desc'):
        query = db.session.query(cls)
        if search is not None and search != '':
            if search.find('|') != -1:
                tmp = search.split('|')
                conditions = []
                for tt in tmp:
                    if tt != '':
                        conditions.append(cls.remote_path.like('%'+tt.strip()+'%') )
                        conditions.append(cls.folder_id.like('%'+tt.strip()+'%') )
                query = query.filter(or_(*conditions))
            elif search.find(',') != -1:
                tmp = search.split(',')
                conditions = []
                for tt in tmp:
                    if tt != '':
                        conditions.append(cls.remote_path.like('%'+tt.strip()+'%') )
                        conditions.append(cls.folder_id.like('%'+tt.strip()+'%') )
                        query = query.filter(or_(*conditions))
            else:
                query = query.filter(or_(cls.remote_path.like('%'+search+'%'), cls.folder_id.like('%'+search+'%')))

        if order == 'ctime_desc':
            query = query.order_by(desc(cls.created_time))
        elif order == 'ctime_asc':
            query = query.order_by(cls.created_time)
        elif order == 'mtime_desc':
            query = query.order_by(desc(cls.last_updated_time))
        elif order == 'mtime_asc':
            query = query.order_by(cls.last_updated_time)
        else:
            query = query.order_by(desc(cls.id))

        return query 

class ModelScanItem(db.Model):
    __tablename__ = '%s_scan_item' % (package_name)
    __table_args__ = {'mysql_collate': 'utf8_general_ci'}
    __bind_key__ = package_name

    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    reserved = db.Column(db.JSON)
    watch_id = db.Column(db.Integer)
    remote_name = db.Column(db.String)
    remote_path = db.Column(db.String)
    folder_id = db.Column(db.String)
    parent_id = db.Column(db.String)
    plex_path = db.Column(db.String)
    status = db.Column(db.String)
    updated_time = db.Column(db.DateTime)

    def __init__(self, watch_id, remote_name, remote_path, folder_id, parent_id, plex_path):
        self.created_time = datetime.now()
        self.watch_id = watch_id
        self.remote_name = py_unicode(remote_name)
        self.remote_path = py_unicode(remote_path)
        self.folder_id = py_unicode(folder_id)
        self.parent_id = py_unicode(parent_id)
        self.plex_path = py_unicode(plex_path)
        self.status = 'created'
        self.updated_time = None

    def __repr__(self):
        return repr(self.as_dict())

    def as_dict(self):
        ret = {x.name: getattr(self, x.name) for x in self.__table__.columns}
        ret['created_time'] = self.created_time.strftime('%m-%d %H:%M:%S') 
        ret['updated_time'] = u'-' if self.updated_time == None else self.updated_time.strftime('%m-%d %H:%M:%S') 
        return ret

    def save(self):
        db.session.add(self)
        db.session.commit()

    @classmethod
    def delete(cls, id):
        db.session.query(cls).filter_by(id=id).delete()
        db.session.commit()

    @classmethod
    def get_by_id(cls, id):
        return db.session.query(cls).filter_by(id=id).first()
    
    @classmethod
    def get_by_folder_id(cls, folder_id):
        return db.session.query(cls).filter_by(folder_id=folder_id).first()

    @classmethod
    def get_by_plex_path(cls, plex_path):
        return db.session.query(cls).filter_by(plex_path=plex_path).first()

    @classmethod
    def get_all_entities(cls):
        return db.session.query(cls).all()

    @classmethod
    def get_all_entities(cls):
        return db.session.query(cls).all()

    @classmethod
    def web_list(cls, req):
        try:
            ret = {}
            page = 1
            page_size = 30
            search = ''
            order='ctime_desc'
            if 'page' in req.form:
                page = int(req.form['page'])
            if 'search_word' in req.form:
                search = req.form['search_word']
            if 'order' in req.form:
                order = req.form['order']
            query = cls.make_query(search=search, order=order)
            count = query.count()
            query = query.limit(page_size).offset((page-1)*page_size)
            #logger.debug('cls count:%s', count)
            lists = query.all()
            ret['list'] = [item.as_dict() for item in lists]
            ret['paging'] = Util.get_paging_info(count, page, page_size)
            return ret
        except Exception as e:
            logger.error('Exception:%s', e)
            logger.error(traceback.format_exc())


    @classmethod
    def make_query(cls, search='', order='ctime_desc'):
        query = db.session.query(cls)
        if search is not None and search != '':
            if search.find('|') != -1:
                tmp = search.split('|')
                conditions = []
                for tt in tmp:
                    if tt != '':
                        conditions.append(cls.remote_path.like('%'+tt.strip()+'%') )
                        conditions.append(cls.folder_id.like('%'+tt.strip()+'%') )
                query = query.filter(or_(*conditions))
            elif search.find(',') != -1:
                tmp = search.split(',')
                conditions = []
                for tt in tmp:
                    if tt != '':
                        conditions.append(cls.remote_path.like('%'+tt.strip()+'%') )
                        conditions.append(cls.folder_id.like('%'+tt.strip()+'%') )
                        query = query.filter(or_(*conditions))
            else:
                query = query.filter(or_(cls.remote_path.like('%'+search+'%'), cls.folder_id.like('%'+search+'%')))

        if order == 'ctime_desc':
            query = query.order_by(desc(cls.created_time))
        elif order == 'ctime_asc':
            query = query.order_by(cls.created_time)
        elif order == 'mtime_desc':
            query = query.order_by(desc(cls.updated_time))
        elif order == 'mtime_asc':
            query = query.order_by(cls.updated_time)
        else:
            query = query.order_by(desc(cls.id))

        return query 
