"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import json
import logging
import os
from pkg_resources import parse_version

try:
    # py2
    import urlparse
except ImportError:
    # py3
    import urllib.parse as urlparse

from osbs.build.manipulate import DockJsonManipulator
from osbs.build.spec import CommonSpec, ProdSpec, SimpleSpec
from osbs.constants import PROD_BUILD_TYPE, SIMPLE_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE
from osbs.constants import PROD_WITH_SECRET_BUILD_TYPE
from osbs.constants import SECRETS_PATH
from osbs.exceptions import OsbsException, OsbsValidationException
from osbs.utils import looks_like_git_hash


build_classes = {}
logger = logging.getLogger(__name__)


def register_build_class(cls):
    build_classes[cls.key] = cls
    return cls


class BuildRequest(object):
    """
    Wraps logic for creating build inputs
    """

    key = None

    def __init__(self, build_json_store):
        """
        :param build_json_store: str, path to directory with JSON build files
        """
        self.spec = None
        self.build_json_store = build_json_store
        self.build_json = None       # rendered template
        self._template = None        # template loaded from filesystem
        self._inner_template = None  # dock json
        self._dj = None
        self._resource_limits = None
        self._openshift_required_version = parse_version('0.5.4')

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        :param kwargs:
        :return:
        """
        raise NotImplementedError()

    def set_resource_limits(self, cpu=None, memory=None, storage=None):
        if self._resource_limits is None:
            self._resource_limits = {}

        if cpu is not None:
            self._resource_limits['cpu'] = cpu

        if memory is not None:
            self._resource_limits['memory'] = memory

        if storage is not None:
            self._resource_limits['storage'] = storage

    def set_openshift_required_version(self, openshift_required_version):
        if openshift_required_version is not None:
            self._openshift_required_version = openshift_required_version

    @staticmethod
    def new_by_type(build_name, *args, **kwargs):
        """Find BuildRequest with the given name."""

        # Compatibility
        if build_name in (PROD_WITHOUT_KOJI_BUILD_TYPE,
                          PROD_WITH_SECRET_BUILD_TYPE):
            build_name = PROD_BUILD_TYPE

        try:
            build_class = build_classes[build_name]
            logger.debug("Instantiating: %s(%s, %s)", build_class.__name__, args, kwargs)
            return build_class(*args, **kwargs)
        except KeyError:
            raise RuntimeError("Unknown build type '{0}'".format(build_name))

    def render(self, validate=True):
        """
        render input parameters into template

        :return: dict, build json
        """
        raise NotImplementedError()

    @property
    def build_id(self):
        return self.build_json['metadata']['name']

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self.build_json_store, "%s.json" % self.key)
            logger.debug("loading template from path %s", path)
            try:
                with open(path, "r") as fp:
                    self._template = json.load(fp)
            except (IOError, OSError) as ex:
                raise OsbsException("Can't open template '%s': %s" %
                                    (path, repr(ex)))
        return self._template

    @property
    def inner_template(self):
        if self._inner_template is None:
            path = os.path.join(self.build_json_store, "%s_inner.json" % self.key)
            logger.debug("loading inner template from path %s", path)
            with open(path, "r") as fp:
                self._inner_template = json.load(fp)
        return self._inner_template

    @property
    def dj(self):
        if self._dj is None:
            self._dj = DockJsonManipulator(self.template, self.inner_template)
        return self._dj

    def is_auto_instantiated(self):
        """Return True if this BuildConfig will be automatically instantiated when created."""
        triggers = self.template['spec'].get('triggers', [])
        for trigger in triggers:
            if trigger['type'] == 'ImageChange' and \
                    trigger['imageChange']['from']['kind'] == 'ImageStreamTag':
                return True
        return False


class CommonBuild(BuildRequest):
    def __init__(self, build_json_store):
        """
        :param build_json_store: str, path to directory with JSON build files
        """
        super(CommonBuild, self).__init__(build_json_store)
        self.spec = CommonSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param git_uri: str, URL of source git repository
        :param git_ref: str, what git tree to build (default: master)
        :param registry_uris: list, URI of docker registry where built image is pushed (str)
        :param source_registry_uri: str, URI of docker registry from which image is pulled
        :param user: str, user part of resulting image name
        :param component: str, component part of the image name
        :param openshift_uri: str, URL of openshift instance for the build
        :param builder_openshift_url: str, url of OpenShift where builder will connect
        :param yum_repourls: list of str, URLs to yum repo files to include
        :param use_auth: bool, use auth from atomic-reactor?
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render_resource_limits(self):
        if self._resource_limits is not None:
            resources = self.template['spec'].get('resources', {})
            limits = resources.get('limits', {})
            limits.update(self._resource_limits)
            resources['limits'] = limits
            self.template['spec']['resources'] = resources

    def render_tag_and_push_registries(self):
        if self.dj.dock_json_has_plugin_conf('postbuild_plugins',
                                             'tag_and_push'):
            push_conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins',
                                                          'tag_and_push')
            args = push_conf.setdefault('args', {})
            registries = args.setdefault('registries', {})
            placeholder = '{{REGISTRY_URI}}'

            if placeholder in registries:
                for registry in self.spec.registry_uris.value:
                    if not registry.uri:
                        continue

                    regdict = registries[placeholder].copy()
                    regdict['version'] = registry.version
                    registries[registry.docker_uri] = regdict

                del registries[placeholder]

    def render_add_yum_repo_by_url(self):
        if (self.spec.yum_repourls.value is not None and
                self.dj.dock_json_has_plugin_conf('prebuild_plugins',
                                                  "add_yum_repo_by_url")):
            self.dj.dock_json_set_arg('prebuild_plugins',
                                      "add_yum_repo_by_url", "repourls",
                                      self.spec.yum_repourls.value)
            if self.spec.proxy.value:
                self.dj.dock_json_set_arg('prebuild_plugins',
                                          "add_yum_repo_by_url", "inject_proxy",
                                          self.spec.proxy.value)

    def render_check_and_set_rebuild(self, use_auth=None):
        if self.dj.dock_json_has_plugin_conf('prebuild_plugins',
                                             'check_and_set_rebuild'):
            self.dj.dock_json_set_arg('prebuild_plugins',
                                      'check_and_set_rebuild', 'url',
                                      self.spec.builder_openshift_url.value)
            if use_auth is not None:
                self.dj.dock_json_set_arg('prebuild_plugins',
                                          'check_and_set_rebuild',
                                          'use_auth', use_auth)

    def render_store_metadata_in_osv3(self, use_auth=None):
        try:
            self.dj.dock_json_set_arg('exit_plugins', "store_metadata_in_osv3",
                                      "url",
                                      self.spec.builder_openshift_url.value)

            if use_auth is not None:
                self.dj.dock_json_set_arg('exit_plugins',
                                          "store_metadata_in_osv3",
                                          "use_auth", use_auth)
        except RuntimeError:
            # For compatibility with older osbs.conf files
            self.dj.dock_json_set_arg('postbuild_plugins',
                                      "store_metadata_in_osv3",
                                      "url",
                                      self.spec.builder_openshift_url.value)

            if use_auth is not None:
                # For compatibility with older osbs.conf files
                self.dj.dock_json_set_arg('postbuild_plugins',
                                          "store_metadata_in_osv3",
                                          "use_auth", use_auth)

    def render(self):
        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        self.template['metadata']['name'] = self.spec.name.value
        self.render_resource_limits()
        self.template['spec']['source']['git']['uri'] = self.spec.git_uri.value
        self.template['spec']['source']['git']['ref'] = self.spec.git_ref.value

        if len(self.spec.registry_uris.value) > 0:
            primary_registry_uri = self.spec.registry_uris.value[0].docker_uri
            tag_with_registry = '{0}/{1}'.format(primary_registry_uri,
                                                 self.spec.image_tag.value)
            self.template['spec']['output']['to']['name'] = tag_with_registry
        else:
            self.template['spec']['output']['to']['name'] = self.spec.image_tag.value

        self.render_tag_and_push_registries()

        if 'triggers' in self.template['spec']:
            imagechange = self.template['spec']['triggers'][0]['imageChange']
            imagechange['from']['name'] = self.spec.trigger_imagestreamtag.value

        self.render_add_yum_repo_by_url()

        use_auth = self.spec.use_auth.value
        self.render_check_and_set_rebuild(use_auth=use_auth)
        self.render_store_metadata_in_osv3(use_auth=use_auth)

        # For Origin 1.0.6 we'll use the 'secrets' array; for earlier
        # versions we'll just use 'sourceSecret'
        if self._openshift_required_version < parse_version('1.0.6'):
            if 'secrets' in self.template['spec']['strategy']['customStrategy']:
                del self.template['spec']['strategy']['customStrategy']['secrets']
        else:
            if 'sourceSecret' in self.template['spec']['source']:
                del self.template['spec']['source']['sourceSecret']

        if self.spec.build_imagestream.value:
            self.template['spec']['strategy']['customStrategy']['from']['kind'] = 'ImageStreamTag'
            self.template['spec']['strategy']['customStrategy']['from']['name'] = \
                self.spec.build_imagestream.value
        else:
            self.template['spec']['strategy']['customStrategy']['from']['name'] = \
                self.spec.build_image.value

    def validate_input(self):
        self.spec.validate()


@register_build_class
class ProductionBuild(CommonBuild):
    key = PROD_BUILD_TYPE

    def __init__(self, build_json_store, **kwargs):
        super(ProductionBuild, self).__init__(build_json_store, **kwargs)
        self.spec = ProdSpec()

        # For the koji "scratch" build type
        self.scratch = False

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param pulp_secret: str, resource name of pulp secret
        :param pdc_secret: str, resource name of pdc secret
        :param koji_target: str, koji tag with packages used to build the image
        :param kojiroot: str, URL from which koji packages are fetched
        :param kojihub: str, URL of the koji hub
        :param koji_certs_secret: str, resource name of secret that holds the koji certificates
        :param pulp_registry: str, name of pulp registry in dockpulp.conf
        :param nfs_server_path: str, NFS server and path
        :param nfs_dest_dir: str, directory to create on NFS server
        :param sources_command: str, command used to fetch dist-git sources
        :param architecture: str, architecture we are building for
        :param vendor: str, vendor name
        :param build_host: str, host the build will run on or None for auto
        :param authoritative_registry: str, the docker registry authoritative for this image
        :param distribution_scope: str, distribution scope for this image
                                   (private, authoritative-source-only, restricted, public)
        :param use_auth: bool, use auth from atomic-reactor?
        :param git_push_url: str, URL for git push
        """

        # Here we cater to the koji "scratch" build type, this will disable
        # all plugins that might cause importing of data to koji
        try:
            self.scratch = kwargs.pop("scratch")
        except KeyError:
            pass

        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def set_secret_for_plugin(self, plugin, secret):
        has_plugin_conf = self.dj.dock_json_has_plugin_conf(plugin[0],
                                                            plugin[1])
        if 'secrets' in self.template['spec']['strategy']['customStrategy']:
            if has_plugin_conf:
                # origin 1.0.6 and newer
                secret_path = os.path.join(SECRETS_PATH, secret)
                logger.info("Configuring %s secret at %s", secret, secret_path)
                custom = self.template['spec']['strategy']['customStrategy']
                existing = [secret_mount for secret_mount in custom['secrets']
                            if secret_mount['secretSource']['name'] == secret]
                if existing:
                    logger.debug("secret %s already set", plugin[1])
                else:
                    custom['secrets'].append({
                        'secretSource': {
                            'name': secret,
                        },
                        'mountPath': secret_path,
                    })

                self.dj.dock_json_set_arg(*(plugin + (secret_path,)))
            else:
                logger.debug("not setting secret for unused plugin %s",
                             plugin[1])

        elif plugin[1] in ('pulp_push', 'pulp_sync'):
            # setting pulp_push/pulp_sync secret for origin 1.0.5 and earlier
            #  we only use this way to preserve backwards compat for pulp_push plugin,
            #  other plugins must use the new secrets way above
            logger.info("Configuring %s secret as sourceSecret", secret)
            if 'sourceSecret' not in self.template['spec']['source']:
                raise OsbsValidationException("JSON template does not allow secrets")

            old_secret = self.template['spec']['source']['sourceSecret'].get('name')
            if old_secret and old_secret != secret and not old_secret.startswith("{{"):
                raise OsbsValidationException("Not possible to set two different source secrets")

            self.template['spec']['source']['sourceSecret']['name'] = secret

        elif has_plugin_conf:
            raise OsbsValidationException("cannot set more than one secret "
                                          "unless using OpenShift >= 1.0.6")

    def set_secrets(self, secrets):
        """
        :param secrets: dict, {(plugin type, plugin name, argument name): secret name}
            for example {('exit_plugins', 'sendmail', 'pdc_secret_path'): 'pdc_secret', ...}
        """
        secret_set = False
        for (plugin, secret) in secrets.items():
            if not isinstance(plugin, tuple) or len(plugin) != 3:
                raise ValueError('got "%s" as secrets key, need 3-tuple' % plugin)
            if secret is not None:
                self.set_secret_for_plugin(plugin, secret)
                secret_set = True

        if not secret_set:
            # remove references to secret if no secret was set
            if 'sourceSecret' in self.template['spec']['source']:
                del self.template['spec']['source']['sourceSecret']
            if 'secrets' in self.template['spec']['strategy']['customStrategy']:
                del self.template['spec']['strategy']['customStrategy']['secrets']

    @staticmethod
    def remove_tag_and_push_registries(tag_and_push_registries, version):
        """
        Remove matching entries from tag_and_push_registries (in-place)

        :param tag_and_push_registries: dict, uri -> dict
        :param version: str, 'version' to match against
        """
        registries = [uri
                      for uri, regdict in tag_and_push_registries.items()
                      if regdict['version'] == version]
        for registry in registries:
            logger.info("removing %s registry: %s", version, registry)
            del tag_and_push_registries[registry]

    def adjust_for_registry_api_versions(self):
        """
        Enable/disable plugins depending on supported registry API versions
        """

        versions = self.spec.registry_api_versions.value

        try:
            push_conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins',
                                                          'tag_and_push')
            tag_and_push_registries = push_conf['args']['registries']
        except (KeyError, IndexError):
            tag_and_push_registries = {}

        if 'v1' not in versions:
            # Remove v1-only plugins
            for phase, name in [('postbuild_plugins', 'pulp_push')]:
                logger.info("removing v1-only plugin: %s", name)
                self.dj.remove_plugin(phase, name)

            # remove extra tag_and_push config
            self.remove_tag_and_push_registries(tag_and_push_registries, 'v1')

        if 'v2' not in versions:
            # Remove v2-only plugins
            logger.info("removing v2-only plugin: pulp_sync")
            self.dj.remove_plugin('postbuild_plugins', 'pulp_sync')

            # remove extra tag_and_push config
            self.remove_tag_and_push_registries(tag_and_push_registries, 'v2')

        # Remove 'version' from tag_and_push plugin config as it's no
        # longer needed
        for regdict in tag_and_push_registries.values():
            if 'version' in regdict:
                del regdict['version']

    def adjust_for_triggers(self):
        """
        Remove trigger-related plugins if no triggers set

        If there are no triggers set, there is no point in running
        the check_and_set_rebuild, bump_release, or import_image plugins.
        """
        triggers = self.template['spec'].get('triggers', [])
        if len(triggers) == 0:
            for when, which in [("prebuild_plugins", "check_and_set_rebuild"),
                                ("prebuild_plugins", "stop_autorebuild_if_disabled"),
                                ("prebuild_plugins", "bump_release"),
                                ("postbuild_plugins", "import_image"),
                                ("exit_plugins", "sendmail")]:
                logger.info("removing %s from request because there are no triggers",
                            which)
                self.dj.remove_plugin(when, which)

    def adjust_for_scratch(self):
        """
        Remove koji Content Generator related plugins if no triggers set in
        order to hadle the "scratch build" scenario
        """
        if self.scratch:
            # Note: only one for now, but left in a list like other adjust_for_
            # functions in the event that this needs to be expanded
            for when, which in [
                ("exit_plugins", "koji_promote"),
            ]:
                logger.info("removing %s from request because there are no triggers",
                            which)
                self.dj.remove_plugin(when, which)

    def render_add_labels_in_dockerfile(self):
        implicit_labels = {
            'Vendor': self.spec.vendor.value,
            'Authoritative_Registry': self.spec.authoritative_registry.value,
            'distribution-scope': self.spec.distribution_scope.value,
        }

        build_host = self.spec.build_host.value
        if build_host:
            implicit_labels['Build_Host'] = build_host

        architecture = self.spec.architecture.value
        if architecture:
            implicit_labels['Architecture'] = architecture

        self.dj.dock_json_merge_arg('prebuild_plugins',
                                    "add_labels_in_dockerfile",
                                    "labels", implicit_labels)

    def render_koji(self):
        """
        if there is yum repo specified, don't pick stuff from koji
        """
        if self.spec.yum_repourls.value:
            logger.info("removing koji from request "
                        "because there is yum repo specified")
            self.dj.remove_plugin("prebuild_plugins", "koji")
        elif not (self.spec.koji_target.value and
                  self.spec.kojiroot.value and
                  self.spec.kojihub.value):
            logger.info("removing koji from request as not specified")
            self.dj.remove_plugin("prebuild_plugins", "koji")
        else:
            self.dj.dock_json_set_arg('prebuild_plugins', "koji",
                                      "target", self.spec.koji_target.value)
            self.dj.dock_json_set_arg('prebuild_plugins', "koji",
                                      "root", self.spec.kojiroot.value)
            self.dj.dock_json_set_arg('prebuild_plugins', "koji",
                                      "hub", self.spec.kojihub.value)
            if self.spec.proxy.value:
                self.dj.dock_json_set_arg('prebuild_plugins', "koji",
                                          "proxy", self.spec.proxy.value)

    def render_bump_release(self):
        """
        If the bump_release plugin is present, configure it
        """
        if self.dj.dock_json_has_plugin_conf('prebuild_plugins',
                                             'bump_release'):
            push_url = self.spec.git_push_url.value

            if push_url is not None:
                # Do we need to add in a username?
                if self.spec.git_push_username.value is not None:
                    components = urlparse.urlsplit(push_url)

                    # Remove any existing username
                    netloc = components.netloc.split('@', 1)[-1]

                    # Add in the configured username
                    comps = list(components)
                    comps[1] = "%s@%s" % (self.spec.git_push_username.value,
                                          netloc)

                    # Reassemble the URL
                    push_url = urlparse.urlunsplit(comps)

                self.dj.dock_json_set_arg('prebuild_plugins', 'bump_release',
                                          'push_url', push_url)

            # Set the source git ref to the branch we're building
            # from, but configure the plugin with the commit hash we
            # started with.
            logger.info("bump_release configured so "
                        "setting source git ref to %s",
                        self.spec.git_branch.value)

            if looks_like_git_hash(self.spec.git_branch.value):
                raise OsbsValidationException("git_branch parameter requires "
                                              "branch name not hash")

            if not looks_like_git_hash(self.spec.git_ref.value):
                raise OsbsValidationException("git_ref parameter requires "
                                              "hash not branch name")

            self.template['spec']['source']['git']['ref'] = \
                self.spec.git_branch.value
            self.dj.dock_json_set_arg('prebuild_plugins', 'bump_release',
                                      'git_ref', self.spec.git_ref.value)

    def render_koji_promote(self, use_auth=None):
        if not self.dj.dock_json_has_plugin_conf('exit_plugins',
                                                 'koji_promote'):
            return

        if self.spec.kojihub.value:
            self.dj.dock_json_set_arg('exit_plugins', 'koji_promote', 'url',
                                      self.spec.builder_openshift_url.value)
            self.dj.dock_json_set_arg('exit_plugins', 'koji_promote',
                                      'kojihub', self.spec.kojihub.value)

            if use_auth is not None:
                self.dj.dock_json_set_arg('exit_plugins', 'koji_promote',
                                          'use_auth', use_auth)
        else:
            logger.info("removing koji_promote from request as no kojihub "
                        "specified")
            self.dj.remove_plugin("exit_plugins", "koji_promote")

    def render_sendmail(self):
        """
        if we have pdc_url and smtp_uri, configure sendmail plugin,
        else remove it
        """
        if not self.dj.dock_json_has_plugin_conf('exit_plugins', 'sendmail'):
            return

        if self.spec.pdc_url.value and self.spec.smtp_uri.value:
            self.dj.dock_json_set_arg('exit_plugins', 'sendmail', 'url',
                                      self.spec.builder_openshift_url.value)
            self.dj.dock_json_set_arg('exit_plugins', 'sendmail', 'pdc_url',
                                      self.spec.pdc_url.value)
            self.dj.dock_json_set_arg('exit_plugins', 'sendmail', 'smtp_uri',
                                      self.spec.smtp_uri.value)
            self.dj.dock_json_set_arg('exit_plugins', 'sendmail', 'submitter',
                                      self.spec.user.value)
            # make sure we'll be able to authenticate to PDC
            if 'pdc_secret_path' not in \
                    self.dj.dock_json_get_plugin_conf('exit_plugins',
                                                      'sendmail')['args']:
                raise OsbsValidationException('sendmail plugin configured, '
                                              'but no pdc_secret_path')
        else:
            logger.info("removing sendmail from request, "
                        "requires pdc_url and smtp_uri")
            self.dj.remove_plugin('exit_plugins', 'sendmail')

    def render_pulp_push(self):
        """
        If a pulp registry is specified, use the pulp plugin
        """
        if not self.dj.dock_json_has_plugin_conf('postbuild_plugins',
                                                 'pulp_push'):
            return

        pulp_registry = self.spec.pulp_registry.value
        if pulp_registry:
            self.dj.dock_json_set_arg('postbuild_plugins', 'pulp_push',
                                      'pulp_registry_name', pulp_registry)

            # Verify we have either a secret or username/password
            if self.spec.pulp_secret.value is None:
                conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins',
                                                         'pulp_push')
                args = conf.get('args', {})
                if 'username' not in args:
                    raise OsbsValidationException("Pulp registry specified "
                                                  "but no auth config")
        else:
            # If no pulp registry is specified, don't run the pulp plugin
            logger.info("removing pulp_push from request, "
                        "requires pulp_registry")
            self.dj.remove_plugin("postbuild_plugins", "pulp_push")

    def render_pulp_sync(self):
        """
        If a pulp registry is specified, use the pulp plugin
        """
        if not self.dj.dock_json_has_plugin_conf('postbuild_plugins',
                                                 'pulp_sync'):
            return

        pulp_registry = self.spec.pulp_registry.value
        docker_v2_registries = [registry
                                for registry in self.spec.registry_uris.value
                                if registry.version == 'v2']

        if pulp_registry and docker_v2_registries:
            self.dj.dock_json_set_arg('postbuild_plugins', 'pulp_sync',
                                      'pulp_registry_name', pulp_registry)

            # First specified v2 registry is the one we'll tell pulp
            # to sync from. Keep the http prefix -- pulp wants it.
            docker_registry = docker_v2_registries[0].uri
            logger.info("using docker v2 registry %s for pulp_sync",
                        docker_registry)

            self.dj.dock_json_set_arg('postbuild_plugins', 'pulp_sync',
                                      'docker_registry', docker_registry)

            # Verify we have either a secret or username/password
            if self.spec.pulp_secret.value is None:
                conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins',
                                                         'pulp_sync')
                args = conf.get('args', {})
                if 'username' not in args:
                    raise OsbsValidationException("Pulp registry specified "
                                                  "but no auth config")
        else:
            # If no pulp registry is specified, don't run the pulp plugin
            logger.info("removing pulp_sync from request, "
                        "requires pulp_registry and a v2 registry")
            self.dj.remove_plugin("postbuild_plugins", "pulp_sync")

    def render_import_image(self, use_auth=None):
        """
        Configure the import_image plugin
        """
        if self.dj.dock_json_has_plugin_conf('postbuild_plugins',
                                             'import_image'):
            self.dj.dock_json_set_arg('postbuild_plugins', 'import_image',
                                      'imagestream',
                                      self.spec.imagestream_name.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'import_image',
                                      'docker_image_repo',
                                      self.spec.imagestream_url.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'import_image',
                                      'url',
                                      self.spec.builder_openshift_url.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'import_image',
                                      'build_json_dir',
                                      self.spec.builder_build_json_dir.value)

            use_auth = self.spec.use_auth.value
            if use_auth is not None:
                self.dj.dock_json_set_arg('postbuild_plugins', 'import_image',
                                          'use_auth', use_auth)

            if self.spec.imagestream_insecure_registry.value:
                self.dj.dock_json_set_arg('postbuild_plugins', 'import_image',
                                          'insecure_registry', True)

    def render(self, validate=True):
        if validate:
            self.spec.validate()
        super(ProductionBuild, self).render()

        self.dj.dock_json_set_arg('prebuild_plugins', "distgit_fetch_artefacts",
                                  "command", self.spec.sources_command.value)

        # pull_base_image wants a docker URI so strip off the scheme part
        source_registry = self.spec.source_registry_uri.value
        self.dj.dock_json_set_arg('prebuild_plugins', "pull_base_image", "parent_registry",
                                  source_registry.docker_uri if source_registry else None)

        # The rebuild trigger requires git_branch and git_push_url
        # parameters, but those parameters are optional. If either was
        # not provided, remove the trigger.
        remove_triggers = False
        for param_name in ['git_branch', 'git_push_url']:
            param = getattr(self.spec, param_name)
            if not param.value:
                logger.info("removing triggers as no %s specified", param_name)
                remove_triggers = True
                # Continue the loop so we log everything that's missing

        if remove_triggers and 'triggers' in self.template['spec']:
            del self.template['spec']['triggers']

        self.adjust_for_triggers()
        self.adjust_for_scratch()

        # Enable/disable plugins as needed for target registry API versions
        self.adjust_for_registry_api_versions()

        self.set_secrets({('postbuild_plugins',
                           'pulp_push',
                           'pulp_secret_path'):
                          self.spec.pulp_secret.value,

                          ('postbuild_plugins',
                           'pulp_sync',
                           'pulp_secret_path'):
                          self.spec.pulp_secret.value,

                          ('exit_plugins', 'sendmail', 'pdc_secret_path'):
                          self.spec.pdc_secret.value,

                          ('exit_plugins', 'koji_promote', 'koji_ssl_certs'):
                          self.spec.koji_certs_secret.value})

        if self.spec.pulp_secret.value:
            # Don't push to docker registry, we're using pulp here
            # but still construct the unique tag
            self.template['spec']['output']['to']['name'] = \
                self.spec.image_tag.value

        use_auth = self.spec.use_auth.value
        self.render_add_labels_in_dockerfile()
        self.render_koji()
        self.render_bump_release()
        self.render_import_image(use_auth=use_auth)
        self.render_pulp_push()
        self.render_pulp_sync()
        self.render_koji_promote(use_auth=use_auth)
        self.render_sendmail()

        self.dj.write_dock_json()
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json


@register_build_class
class SimpleBuild(CommonBuild):
    """
    Simple build type for scratch builds - gets sources from git, builds image
    according to Dockerfile, pushes it to a registry.
    """

    key = SIMPLE_BUILD_TYPE

    def __init__(self, build_json_store, **kwargs):
        super(SimpleBuild, self).__init__(build_json_store, **kwargs)
        self.spec = SimpleSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self, validate=True):
        if validate:
            self.spec.validate()
        super(SimpleBuild, self).render()
        try:
            self.dj.dock_json_set_arg('exit_plugins', "store_metadata_in_osv3", "url",
                                      self.spec.builder_openshift_url.value)
        except RuntimeError:
            # For compatibility with older osbs.conf files
            self.dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3", "url",
                                      self.spec.builder_openshift_url.value)

        # Remove 'version' from tag_and_push plugin config as it's no
        # longer needed
        if self.dj.dock_json_has_plugin_conf('postbuild_plugins',
                                             'tag_and_push'):
            push_conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins',
                                                          'tag_and_push')
            try:
                registries = push_conf['args']['registries']
            except KeyError:
                pass
            else:
                for regdict in registries.values():
                    if 'version' in regdict:
                        del regdict['version']

        self.dj.write_dock_json()
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json


class BuildManager(object):

    def __init__(self, build_json_store):
        self.build_json_store = build_json_store

    def get_build_request_by_type(self, build_type):
        """
        return instance of BuildRequest according to specified build type

        :param build_type: str, name of build type
        :return: instance of BuildRequest
        """
        b = BuildRequest.new_by_type(build_type, build_json_store=self.build_json_store)
        return b
