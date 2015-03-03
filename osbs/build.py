from __future__ import print_function, absolute_import, unicode_literals

import copy
import json
import os
import datetime
from osbs.constants import BUILD_JSON_STORE, DEFAULT_GIT_REF


class DockJsonManipulator(object):
    """ """
    def __init__(self, build_json):
        """ """
        self.build_json = build_json
        self.dock_json = self.get_dock_json()

    def get_dock_json(self):
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


class Build(object):
    """ """

    key = None

    def __init__(self, build_json_store):
        """ """
        self.build_json = None  # rendered template
        self._template = None  # template loaded from filesystem
        self.build_json_store = build_json_store

    def render(self):
        """ fill in input template of build json """
        raise NotImplemented()

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
       

class ProductionBuild(Build):
    """
    """

    key = "prod"

    def __init__(self, git_uri, koji_target, user, component, registry_uri,
                 git_ref=DEFAULT_GIT_REF, **kwargs):
        """ """
        super(ProductionBuild, self).__init__(**kwargs)
        self.git_uri = git_uri
        self.git_ref = git_ref
        self.koji_target = koji_target
        self.user = user
        self.component = component
        self.registry_uri = registry_uri
        d = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.name = "%s-%s" % (self.component, d)

    def render(self):
        config = self.template

        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        config['metadata']['name'] = self.name
        config['parameters']['source']['git']['uri'] = self.git_uri
        config['parameters']['source']['git']['ref'] = self.git_ref

        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        config['parameters']['output']['imageTag'] = "%s/%s:%s-%s" % \
            (self.user, self.component, self.koji_target, timestamp)

        config['parameters']['output']['registry'] = self.registry_uri

        dj = DockJsonManipulator(config)
        dj.dock_json_set_arg('prebuild_plugins', "koji", "target", self.koji_target)
        dj.write_dock_json()
        self.build_json = config
        return self.build_json


class BuildManager(object):

    def __init__(self, build_json_store):
        self.build_json_store = build_json_store

    def get_prod_build(self, *args, **kwargs):
        kwargs.setdefault("build_json_store", self.build_json_store)
        b = ProductionBuild(*args, **kwargs)
        b.render()
        b.validate_build_json()
        return b
