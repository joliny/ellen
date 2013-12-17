#!/usr/bin/env python
# -*- coding: utf-8 -*-

import urlparse
from StringIO import StringIO

# NOTICE: this module changed name to `configparser` in python 3.x
from ConfigParser import RawConfigParser

from pygit2 import GIT_OBJ_TAG
from pygit2 import GIT_OBJ_BLOB
from pygit2 import GIT_OBJ_TREE
from pygit2 import GIT_OBJ_COMMIT
from pygit2 import GIT_SORT_TOPOLOGICAL

from ellen.utils import JagareError
from ellen.utils.format import format_commit


TREE_ORDER = {
    'tree': 1,
    'submodule': 2,
    'blob': 3,
}


def ls_tree(repository, ref,
            recursive=None, size=None, name_only=None,
            req_path=None, with_commit=False):
    """git ls-tree command, pygit2 wrapper.
    List the contents of a tree object.
    """

    if req_path:
        req_path = _remove_slash(req_path)

    try:
        obj = repository.revparse_single(ref)
    except (ValueError, KeyError):
        raise JagareError("Reference not found.")

    commit_obj = None

    if obj.type == GIT_OBJ_TREE:
        tree_obj = obj
    elif obj.type == GIT_OBJ_TAG:
        commit_obj = repository.revparse_single(obj.target.hex)
        tree_obj = commit_obj.tree
    elif obj.type == GIT_OBJ_BLOB:
        raise JagareError("Object is blob, doesn't contain any tree")
    elif obj.type == GIT_OBJ_COMMIT:
        commit_obj = obj
        tree_obj = obj.tree

    if req_path:
        tree_entry = tree_obj[req_path]
        tree_obj = repository[tree_entry.oid]
        walker = _walk_tree(tree_obj, req_path)
    else:
        walker = _walk_tree(tree_obj)

    ret_tree = {}
    submodule_obj = None
    submodule = None
    try:
        submodule_obj = repository.revparse_single("%s:.gitmodules" % ref)
        submodule = _parse_submodule(repository, submodule_obj)
    except (ValueError, KeyError):
        # FIXME: return error
        pass
        #raise JagareError("Reference not found.")

    for index, (entry, path) in enumerate(walker):
        mode = '%06o' % entry.filemode
        if mode == '160000':
            objtype = 'submodule'  # For git submodules
        elif mode == '040000':
            objtype = 'tree'
        else:
            objtype = 'blob'
        path = "%s/%s" % (path, entry.name) if path else entry.name

        # FIXME: should get .gitmodules first
        #if path == '.gitmodules':
        #    submodule_obj = entry

        #if recursive or (req_path and req_path.startswith(path)):
        if recursive:
            if objtype == 'tree':
                _tree = repository[entry.oid]
                _tree_list = _walk_tree(_tree, path)
                for _index, _entry in enumerate(_tree_list):
                    if recursive:
                        walker.insert(index + _index + 1, _entry)
                    elif req_path and req_path.startswith(_entry[-1]):
                        walker.insert(index + _index + 1, _entry)
                continue

        #if req_path:
        #    if not path.startswith(req_path):
        #        continue

        if name_only:
            ret_tree[path] = path
            continue

        item = {
            "id": entry.hex,  # FIXME: remove this
            "mode": mode,
            "type": objtype,
            "sha": entry.hex,
            "path": path,
            "name": entry.name
        }

        if item['type'] == 'submodule':
            section_name = ('submodule "{submodule_name}"'
                            .format(submodule_name=path))

            if submodule and submodule.has_section(section_name):
                item['submodule'] = dict(submodule.items(section_name))
                item['submodule']['host'] = _parse_submodule_url(item['submodule']['url'])

                if item['submodule']['url'].endswith('.git'):
                    item['submodule']['url'] = item['submodule']['url'][:-4]
            else:
                item['submodule'] = {}
                item['submodule']['host'] = None
                item['submodule']['url'] = None

        if size:
            if objtype == 'blob':
                blob = repository[entry.oid]
                item['size'] = blob.size
            else:
                item['size'] = '-'

        ret_tree[path] = item

    if name_only:
        return ret_tree.values()

    if with_commit and commit_obj:
        _format_with_last_commit(repository, ret_tree, commit_obj)

    tree_list = ret_tree.values()
    tree_list.sort(key=lambda i: (TREE_ORDER[i['type']], i['name']))
    return tree_list


def _walk_tree(tree, path=None):
    _list = []
    for entry in tree:
        _list.append((entry, path))
    return _list


def _remove_slash(path):
    if path[-1] == '/':
        return path[:-1]
    return path


def _format_submodule_conf(raw):
    if isinstance(raw, unicode):
        lines = raw.splitlines()
    elif isinstance(raw, str):
        lines = raw.decode("UTF-8").splitlines()
    else:
        return None

    lines = map(lambda line: line.strip(), lines)
    return "\n".join(lines)


def _read_blob(repository, sha):
    obj = repository.revparse_single(sha)

    if obj.type != GIT_OBJ_BLOB:
        return None
    return obj.data


def _parse_submodule(repository, submodule_obj):
    # get ref
    #submodule_conf_raw = _read_blob(repository, submodule_obj.hex)
    submodule_conf_raw = submodule_obj.data
    submodule_conf_raw = _format_submodule_conf(submodule_conf_raw)

    config = RawConfigParser(allow_no_value=True)
    config.readfp(StringIO(submodule_conf_raw))

    return config


def _parse_submodule_url(url):
    parser = urlparse.urlparse(url)
    netloc = parser.netloc

    if not netloc:
        # for scp-like url, e.g. git@github.com:xxxx/xxx.git
        if parser.path == url:
            netloc = parser.path.split(':')[0].split('@')[-1]
        else:
            return url

    elif netloc == 'code.dapps.douban.com':
        return 'code'
    elif netloc == 'github.com':
        return 'github'
    elif netloc == 'github-ent.intra.douban.com':
        return 'github-ent'
    return netloc


def _calc_is_changed(commit, path, ret):
    if commit.is_changed([path], no_diff=True)[0]:
        ret[path] = 1


def _format_with_last_commit(repository, ret_tree, to_commit):
    walker = repository.walk(to_commit.oid, GIT_SORT_TOPOLOGICAL)
    paths = [k for k, v in ret_tree.iteritems()]
    ret = {}

    for commit in walker:

        for path in paths:
            _calc_is_changed(commit, path, ret)

        if not ret:
            continue
        fc = format_commit(commit, None)
        for path, r in ret.iteritems():
            ret_tree[path]['commit'] = fc
            paths.remove(path)
        if not paths:
            break
        ret = {}
