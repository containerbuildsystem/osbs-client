from __future__ import print_function, absolute_import, unicode_literals

import copy
import json
import os
import datetime
from osbs.constants import DEFAULT_GIT_REF, POD_FINISHED_STATES, POD_FAILED_STATES, POD_SUCCEEDED_STATES, \
    POD_RUNNING_STATES

build_classes = {}

def register_build_class(cls):
    build_classes[cls.key] = cls
    return cls

class DockJsonManipulator(object):
    """ """
    def __init__(self, build_json, dock_json):
        """ """
        self.build_json = build_json
        self.dock_json = dock_json

    def get_dock_json(self):
        """ return dock json from existing build json """
        env_json = self.build_json['parameters']['strategy']['customStrategy']['env']
        p = [env for env in env_json if env["name"] == "DOCK_PLUGINS"]
        if len(p) <= 0:
            raise RuntimeError("\"env\" misses key DOCK_PLUGINS")
        dock_json_str = p[0]['value']
        dock_json = json.loads(dock_json_str)
        return dock_json

    def dock_json_set_arg(self, plugin_type, plugin_name, arg_key, arg_value):
        try:
            match = [x for x in self.dock_json[plugin_type] if x.get('name', None) == plugin_name]
        except KeyError:
            raise RuntimeError("Invalid dock json: plugin type '%s' misses" % plugin_type)
        if len(match) <= 0:
            raise RuntimeError("no such plugin in dock json: \"%s\"" % plugin_name)
        plugin_conf = match[0]
        plugin_conf['args'][arg_key] = arg_value

    def write_dock_json(self):
        env_json = self.build_json['parameters']['strategy']['customStrategy']['env']
        p = [env for env in env_json if env["name"] == "DOCK_PLUGINS"]
        if len(p) <= 0:
            raise RuntimeError("\"env\" misses key DOCK_PLUGINS")
        p[0]['value'] = json.dumps(self.dock_json)


class BuildRequest(object):
    """ """

    key = None

    def __init__(self, build_json_store, git_uri, user, component, registry_uri,
                 openshift_uri, git_ref=DEFAULT_GIT_REF, **kwargs):
        """ """
        self.build_json = None  # rendered template
        self._template = None  # template loaded from filesystem
        self._inner_template = None  # dock json
        self.build_json_store = build_json_store

        # common template parameters
        self.git_uri = git_uri
        self.git_ref = git_ref
        self.user = user
        self.component = component
        self.registry_uri = registry_uri
        self.openshift_uri = openshift_uri
        d = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.name = "%s-%s" % (self.component, d)

    @staticmethod
    def new_by_type(build_name, *args, **kwargs):
        """Find BuildRequest with the given name."""
        try:
            build_class = build_classes[build_name]
            return build_class(*args, **kwargs)
        except KeyError:
            raise RuntimeError("Unknown build type '{0}'".format(build_name))

    def render(self):
        """ fill in input template of build json """
        config = self.template
        inner_config = self.inner_template
        dj = DockJsonManipulator(config, inner_config)

        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        config['metadata']['name'] = self.name
        config['parameters']['source']['git']['uri'] = self.git_uri
        config['parameters']['source']['git']['ref'] = self.git_ref
        config['parameters']['output']['registry'] = self.registry_uri

        self._render(config, dj)

        dj.write_dock_json()
        self.build_json = config
        return self.build_json

    def validate_input(self):
        """ """

    def validate_build_json(self):
        """ """

    @property
    def build_id(self):
        return self.build_json['metadata']['name']

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self.build_json_store, "%s.json" % self.key)
            with open(path, "r") as fp:
                self._template = json.load(fp)
        return copy.deepcopy(self._template)

    @property
    def inner_template(self):
        if self._inner_template is None:
            path = os.path.join(self.build_json_store, "%s_inner.json" % self.key)
            with open(path, "r") as fp:
                self._template = json.load(fp)
        return copy.deepcopy(self._template)


@register_build_class
class ProductionBuild(BuildRequest):
    """
    """

    key = "prod"

    def __init__(self, koji_target, kojiroot, kojihub, sources_command, **kwargs):
        """ """
        super(ProductionBuild, self).__init__(**kwargs)
        self.koji_target = koji_target
        self.kojiroot = kojiroot
        self.kojihub = kojihub
        self.sources_command = sources_command

    def _render(self, config, dj):
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        config['parameters']['output']['imageTag'] = "%s/%s:%s-%s" % \
            (self.user, self.component, self.koji_target, timestamp)

        dj.dock_json_set_arg('prebuild_plugins', "koji", "target", self.koji_target)
        dj.dock_json_set_arg('prebuild_plugins', "koji", "root", self.kojiroot)
        dj.dock_json_set_arg('prebuild_plugins', "koji", "hub", self.kojihub)
        dj.dock_json_set_arg('prebuild_plugins', "distgit_fetch_artefacts", "command", self.sources_command)
        dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3", "url", self.openshift_uri)


class BuildResponse(object):
    def __init__(self, request):
        """

        :param request: http.Request
        """
        self.request = request
        self._json = None
        self._status = None
        self._build_id = None

    @property
    def json(self):
        if self._json is None:
            self._json = self.request.json()
        return self._json

    @property
    def status(self):
        if self._status is None:
            self._status = self.json['status'].lower()
        return self._status

    @property
    def build_id(self):
        if self._build_id is None:
            self._build_id = self.json['metadata']['name']
        return self._build_id

    def is_finished(self):
        return self.status in POD_FINISHED_STATES

    def is_failed(self):
        return self.status in POD_FAILED_STATES

    def is_succeeded(self):
        return self.status in POD_SUCCEEDED_STATES

    def is_running(self):
        return self.status in POD_RUNNING_STATES


class BuildManager(object):

    def __init__(self, build_json_store):
        self.build_json_store = build_json_store

    def get_build(self, build_type, *args, **kwargs):
        kwargs.setdefault("build_json_store", self.build_json_store)
        b = BuildRequest.new_by_type(build_type, *args, **kwargs)
        b.render()
        b.validate_build_json()
        return b
