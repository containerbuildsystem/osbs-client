from __future__ import print_function, absolute_import, unicode_literals

import copy
import json
import os
import datetime
import collections
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


BuildParam = collections.namedtuple('BuildParam', ['name', 'required'])


class BuildRequest(object):
    """ """

    key = None

    def __init__(self, build_json_store, **kwargs):
        """
        :param build_json_store: str, path to directory with JSON build files
        :param git_uri: str, URL of source git repository
        :param git_ref: str, what git tree to build (default: master)
        :param registry_uri: str, URL of docker registry where built image is pushed
        :param user: str, user part of resulting image name
        :param component: str, component part of the image name
        :param openshift_uri: str, URL of openshift instance for the build

        """
        self.build_json = None  # rendered template
        self._template = None  # template loaded from filesystem
        self._inner_template = None  # dock json
        self.build_json_store = build_json_store

        # common template parameters
        self.param = {}
        self.add_params(kwargs, [
            BuildParam('git_uri', True),
            BuildParam('git_ref', False),
            BuildParam('user', True),
            BuildParam('component', True),
            BuildParam('registry_uri', True),
            BuildParam('openshift_uri', True),
        ])

        d = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.param['name'] = "%s-%s" % (self.param['component'], d)

    def add_params(self, kwargs, params):
        for p in params:
            if p.name in kwargs and kwargs[p.name] is not None:
                self.param[p.name] = kwargs[p.name]
            elif not p.required:
                self.param[p.name] = None
            else:
                raise RuntimeError("Build type {0} requires parameter {1}"
                                   .format(self.key, p.name))

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
        config['metadata']['name'] = self.param['name']
        config['parameters']['source']['git']['uri'] = self.param['git_uri']
        config['parameters']['source']['git']['ref'] = self.param['git_ref'] or DEFAULT_GIT_REF
        config['parameters']['output']['registry'] = self.param['registry_uri']

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

    def __init__(self, **kwargs):
        """
        :param koji_target: str, koji tag with packages used to build the image
        :param kojiroot: str, URL from which koji packages are fetched
        :param kojihub: str, URL of the koji hub
        :param sources_command: str, command used to fetch dist-git sources
        """
        super(ProductionBuild, self).__init__(**kwargs)

        self.add_params(kwargs, [
            BuildParam('koji_target', True),
            BuildParam('kojiroot', True),
            BuildParam('kojihub', True),
            BuildParam('sources_command', True),
        ])

    def _render(self, config, dj):
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        config['parameters']['output']['imageTag'] = "%s/%s:%s-%s" % \
            (self.param['user'], self.param['component'], self.param['koji_target'], timestamp)

        dj.dock_json_set_arg('prebuild_plugins', "koji", "target", self.param['koji_target'])
        dj.dock_json_set_arg('prebuild_plugins', "koji", "root", self.param['kojiroot'])
        dj.dock_json_set_arg('prebuild_plugins', "koji", "hub", self.param['kojihub'])
        dj.dock_json_set_arg('prebuild_plugins', "distgit_fetch_artefacts", "command", self.param['sources_command'])
        dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3", "url", self.param['openshift_uri'])

@register_build_class
class SimpleBuild(BuildRequest):
    """
    Simple build type for scratch builds - gets sources from git, builds image
    according to Dockerfile, pushes it to a registry.
    """

    key = "simple"

    def __init__(self, **kwargs):
        """
        No arguments needed in addition to those of BuildRequest.__init__.
        """
        super(SimpleBuild, self).__init__(**kwargs)
        # only common BuildRequest parameters are used

    def _render(self, config, dj):
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        config['parameters']['output']['imageTag'] = "%s/%s:%s" % \
            (self.param['user'], self.param['component'], timestamp)

        dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3", "url", self.param['openshift_uri'])


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
