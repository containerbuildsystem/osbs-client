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
import re
from pkg_resources import parse_version

try:
    # py2
    import urlparse
    from itertools import izip_longest as zip_longest
except ImportError:
    # py3
    import urllib.parse as urlparse
    from itertools import zip_longest

from osbs.build.manipulate import DockJsonManipulator
from osbs.build.spec import BuildSpec
from osbs.constants import SECRETS_PATH, DEFAULT_OUTER_TEMPLATE, DEFAULT_INNER_TEMPLATE
from osbs.exceptions import OsbsException, OsbsValidationException
from osbs.utils import looks_like_git_hash, git_repo_humanish_part_from_uri


logger = logging.getLogger(__name__)


class BuildRequest(object):
    """
    Wraps logic for creating build inputs
    """

    def __init__(self, build_json_store):
        """
        :param build_json_store: str, path to directory with JSON build files
        """
        self.spec = BuildSpec()
        self.build_json_store = build_json_store
        self.build_json = None       # rendered template
        self._template = None        # template loaded from filesystem
        self._inner_template = None  # dock json
        self._dj = None
        self._resource_limits = None
        self._openshift_required_version = parse_version('1.0.6')
        # For the koji "scratch" build type
        self.scratch = False
        self.base_image = None

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
        :param koji_task_id: int, Koji Task that created this build config
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

        self.base_image = kwargs.get('base_image')

        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

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

    @property
    def build_id(self):
        return self.build_json['metadata']['name']

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self.build_json_store, DEFAULT_OUTER_TEMPLATE)
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
            path = os.path.join(self.build_json_store, DEFAULT_INNER_TEMPLATE)
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

    def set_label(self, name, value):
        self.template['metadata'].setdefault('labels', {})
        self.template['metadata']['labels'][name] = value

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
                for registry, secret in zip_longest(self.spec.registry_uris.value,
                                                    self.spec.registry_secrets.value):
                    if not registry.uri:
                        continue

                    regdict = registries[placeholder].copy()
                    regdict['version'] = registry.version
                    if secret:
                        regdict['secret'] = os.path.join(SECRETS_PATH, secret)

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
        self.dj.dock_json_set_arg('exit_plugins', "store_metadata_in_osv3",
                                  "url",
                                  self.spec.builder_openshift_url.value)

        if use_auth is not None:
            self.dj.dock_json_set_arg('exit_plugins',
                                      "store_metadata_in_osv3",
                                      "use_auth", use_auth)

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

                # there's no need to set args if no plugin secret specified
                # this is used in tag_and_push plugin, as it sets secret path
                # for each registry separately
                if plugin[2] is not None:
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
                if isinstance(secret, list):
                    for secret_item in secret:
                        self.set_secret_for_plugin(plugin, secret_item)
                else:
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

    def is_custom_base_image(self):
        """
        Returns whether or not this is a build from a custom base image
        """
        return bool(re.match('^koji/image-build(:.*)?$',
                             self.base_image or ''))

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
        """
        triggers = self.template['spec'].get('triggers', [])
        if not triggers:
            for when, which in [("prebuild_plugins", "check_and_set_rebuild"),
                                ("prebuild_plugins", "stop_autorebuild_if_disabled"),
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

    def adjust_for_custom_base_image(self):
        """
        Disable plugins to handle builds depending on whether
        or not this is a build from a custom base image.
        """
        plugins = []
        if self.is_custom_base_image():
            # Plugins irrelevant to building base images.
            plugins.append(("prebuild_plugins", "pull_base_image"))
            msg = "removing %s from custom image build request"

        else:
            # Plugins not needed for building non base images.
            plugins.append(("prebuild_plugins", "add_filesystem"))
            msg = "removing %s from non custom image build request"

        for when, which in plugins:
            logger.info(msg, which)
            self.dj.remove_plugin(when, which)

    def render_add_filesystem(self):
        phase = 'prebuild_plugins'
        plugin = 'add_filesystem'

        if self.dj.dock_json_has_plugin_conf(phase, plugin):
            if not self.spec.kojihub.value:
                raise OsbsValidationException(
                    'Custom base image builds require kojihub to be defined')
            self.dj.dock_json_set_arg(phase, plugin, 'koji_hub',
                                      self.spec.kojihub.value)
            if self.spec.proxy.value:
                self.dj.dock_json_set_arg(phase, plugin, 'koji_proxyuser',
                                          self.spec.proxy.value)

    def render_add_labels_in_dockerfile(self):
        phase = 'prebuild_plugins'
        plugin = 'add_labels_in_dockerfile'
        implicit_labels = {}
        label_spec = {
            'Vendor': self.spec.vendor,
            'Authoritative_Registry': self.spec.authoritative_registry,
            'distribution-scope': self.spec.distribution_scope,
            'Build_Host': self.spec.build_host,
            'Architecture': self.spec.architecture,
        }

        for label, spec in label_spec.items():
            if spec.value is not None:
                implicit_labels[label] = spec.value

        self.dj.dock_json_merge_arg(phase, plugin, 'labels', implicit_labels)

        explicit_labels = self.spec.labels.value
        if explicit_labels:
            logger.debug('Adding requested labels: %r', explicit_labels)
            self.dj.dock_json_merge_arg(phase, plugin, "labels",
                                        explicit_labels)

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
        phase = 'prebuild_plugins'
        plugin = 'bump_release'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        target = self.spec.koji_target.value
        hub = self.spec.kojihub.value
        if not (target and hub):
            logger.info('removing %s from request as koji info not specified',
                        plugin)
            self.dj.remove_plugin(phase, plugin)
            return

        self.dj.dock_json_set_arg(phase, plugin, 'target', target)
        self.dj.dock_json_set_arg(phase, plugin, 'hub', hub)

    def render_koji_promote(self, use_auth=None):
        if not self.dj.dock_json_has_plugin_conf('exit_plugins',
                                                 'koji_promote'):
            return

        if self.spec.kojihub.value:
            self.dj.dock_json_set_arg('exit_plugins', 'koji_promote', 'url',
                                      self.spec.builder_openshift_url.value)
            self.dj.dock_json_set_arg('exit_plugins', 'koji_promote',
                                      'kojihub', self.spec.kojihub.value)
            koji_target = self.spec.koji_target.value
            if koji_target is not None:
                self.dj.dock_json_set_arg('exit_plugins', 'koji_promote',
                                          'target', koji_target)

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

        # Find which registry to use
        docker_registry = None
        registry_secret = None
        registries = zip_longest(self.spec.registry_uris.value,
                                 self.spec.registry_secrets.value)
        for registry, secret in registries:
            if registry.version == 'v2':
                # First specified v2 registry is the one we'll tell pulp
                # to sync from. Keep the http prefix -- pulp wants it.
                docker_registry = registry.uri
                registry_secret = secret
                logger.info("using docker v2 registry %s for pulp_sync",
                            docker_registry)
                break

        if pulp_registry and docker_registry:
            self.dj.dock_json_set_arg('postbuild_plugins', 'pulp_sync',
                                      'pulp_registry_name', pulp_registry)

            self.dj.dock_json_set_arg('postbuild_plugins', 'pulp_sync',
                                      'docker_registry', docker_registry)

            if registry_secret:
                self.set_secret_for_plugin(('postbuild_plugins',
                                            'pulp_sync',
                                            'registry_secret_path'),
                                           registry_secret)

            # Verify we have a pulp secret
            if self.spec.pulp_secret.value is None:
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
        if self.spec.imagestream_name is None or self.spec.imagestream_url is None:
            logger.info("removing import_image from request, "
                        "registry or repo url is not defined")
            self.dj.remove_plugin('postbuild_plugins', 'import_image')
            return

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

        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        self.template['metadata']['name'] = self.spec.name.value
        self.render_resource_limits()
        self.template['spec']['source']['git']['uri'] = self.spec.git_uri.value
        self.template['spec']['source']['git']['ref'] = self.spec.git_ref.value

        if self.spec.registry_uris.value:
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

        repo_name = git_repo_humanish_part_from_uri(self.spec.git_uri.value)
        # NOTE: Since only the repo name is used, a forked repos will have
        # the same git-repo-name tag. This is a known limitation. If this
        # use case must be handled properly, the git URI must be taken into
        # account.
        self.set_label('git-repo-name', repo_name)
        if self.spec.git_branch.value:
            self.set_label('git-branch', self.spec.git_branch.value)
        else:
            self.set_label('git-branch', 'unknown')

        if self.spec.sources_command.value is not None:
            self.dj.dock_json_set_arg('prebuild_plugins', "distgit_fetch_artefacts",
                                      "command", self.spec.sources_command.value)
        else:
            logger.info("removing distgit_fetch_artefacts, no sources_command was provided")
            self.dj.remove_plugin('prebuild_plugins', 'distgit_fetch_artefacts')

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

        if self.is_custom_base_image():
            logger.info('removing triggers for custom base image build')
            remove_triggers = True

        if remove_triggers and 'triggers' in self.template['spec']:
            del self.template['spec']['triggers']

        self.adjust_for_triggers()
        self.adjust_for_scratch()
        self.adjust_for_custom_base_image()

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

                          # pulp_sync registry_secret_path set
                          # in render_pulp_sync

                          ('exit_plugins', 'sendmail', 'pdc_secret_path'):
                          self.spec.pdc_secret.value,

                          ('exit_plugins', 'koji_promote', 'koji_ssl_certs'):
                          self.spec.koji_certs_secret.value,

                          ('prebuild_plugins', 'add_filesystem', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('postbuild_plugins', 'tag_and_push',
                           # Only set the secrets for the build, don't
                           # add the path to the plugin's
                           # configuration. This is done elsewhere.
                           None):
                          self.spec.registry_secrets.value})

        if self.spec.pulp_secret.value:
            # Don't push to docker registry, we're using pulp here
            # but still construct the unique tag
            self.template['spec']['output']['to']['name'] = \
                self.spec.image_tag.value

        koji_task_id = self.spec.koji_task_id.value
        if koji_task_id is not None:
            self.template['metadata'].setdefault('labels', {})
            self.template['metadata']['labels']['koji-task-id'] = str(koji_task_id)

        use_auth = self.spec.use_auth.value
        self.render_add_filesystem()
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
