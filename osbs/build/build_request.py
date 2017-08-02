"""
Copyright (c) 2015, 2016, 2017 Red Hat, Inc
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
    from itertools import izip_longest as zip_longest
except ImportError:
    # py3
    from itertools import zip_longest

from osbs.build.manipulate import DockJsonManipulator
from osbs.build.spec import BuildSpec
from osbs.constants import (SECRETS_PATH, DEFAULT_OUTER_TEMPLATE, DEFAULT_INNER_TEMPLATE,
                            DEFAULT_CUSTOMIZE_CONF, BUILD_TYPE_WORKER, BUILD_TYPE_ORCHESTRATOR)
from osbs.exceptions import OsbsException, OsbsValidationException
from osbs.utils import git_repo_humanish_part_from_uri, sanitize_version, Labels
from osbs import __version__ as client_version


logger = logging.getLogger(__name__)


class BuildRequest(object):
    """
    Wraps logic for creating build inputs
    """

    def __init__(self, build_json_store, inner_template=None,
                 outer_template=None, customize_conf=None):
        """
        :param build_json_store: str, path to directory with JSON build files
        :param inner_template: str, path to inner template JSON
        :param outer_template: str, path to outer template JSON
        :param customize_conf: str, path to customize configuration JSON
        """
        self.spec = BuildSpec()
        self.build_json_store = build_json_store
        self._inner_template_path = inner_template or DEFAULT_INNER_TEMPLATE
        self._outer_template_path = outer_template or DEFAULT_OUTER_TEMPLATE
        self._customize_conf_path = customize_conf or DEFAULT_CUSTOMIZE_CONF
        self.build_json = None       # rendered template
        self._template = None        # template loaded from filesystem
        self._inner_template = None  # dock json
        self._customize_conf = None  # site customize conf for _inner_template
        self._dj = None
        self._resource_limits = None
        self._openshift_required_version = parse_version('1.0.6')
        self._repo_info = None
        # For the koji "scratch" build type
        self.scratch = None
        self.base_image = None
        self.scratch_build_node_selector = None
        self.explicit_build_node_selector = None
        self.auto_build_node_selector = None
        self.is_auto = None
        # forward reference
        self.platform_node_selector = None
        self.platform_descriptors = None

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param pulp_secret: str, resource name of pulp secret
        :param koji_target: str, koji tag with packages used to build the image
        :param kojiroot: str, URL from which koji packages are fetched
        :param kojihub: str, URL of the koji hub
        :param koji_certs_secret: str, resource name of secret that holds the koji certificates
        :param koji_task_id: int, Koji Task that created this build config
        :param filesystem_koji_task_id: int, Koji Task that created the base filesystem
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
        :param platform_node_selector: dict, a nodeselector for a specific platform
        :param platform_descriptors: dict, platforms and their archiectures and enable_v1 settings
        :param scratch_build_node_selector: dict, a nodeselector for scratch builds
        :param explicit_build_node_selector: dict, a nodeselector for explicit builds
        :param auto_build_node_selector: dict, a nodeselector for auto builds
        :param is_auto: bool, indicates if build is auto build
        """

        # Here we cater to the koji "scratch" build type, this will disable
        # all plugins that might cause importing of data to koji
        try:
            self.scratch = kwargs.pop("scratch")
        except KeyError:
            pass

        self.base_image = kwargs.get('base_image')
        self.platform_node_selector = kwargs.get('platform_node_selector', {})
        self.platform_descriptors = kwargs.get('platform_descriptors', {})
        self.scratch_build_node_selector = kwargs.get('scratch_build_node_selector', {})
        self.explicit_build_node_selector = kwargs.get('explicit_build_node_selector', {})
        self.auto_build_node_selector = kwargs.get('auto_build_node_selector', {})
        self.is_auto = kwargs.get('is_auto', False)

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

    def set_repo_info(self, repo_info):
        self._repo_info = repo_info

    @property
    def build_id(self):
        return self.build_json['metadata']['name']

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self.build_json_store, self._outer_template_path)
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
            path = os.path.join(self.build_json_store, self._inner_template_path)
            logger.debug("loading inner template from path %s", path)
            with open(path, "r") as fp:
                self._inner_template = json.load(fp)
        return self._inner_template

    @property
    def customize_conf(self):
        if self._customize_conf is None:
            path = os.path.join(self.build_json_store, self._customize_conf_path)
            logger.debug("loading customize conf from path %s", path)
            try:
                with open(path, "r") as fp:
                    self._customize_conf = json.load(fp)
            except IOError:
                # File not found, which is perfectly fine. Set to empty string
                self._customize_conf = {}

        return self._customize_conf

    @property
    def dj(self):
        if self._dj is None:
            self._dj = DockJsonManipulator(self.template, self.inner_template)
        return self._dj

    def has_ist_trigger(self):
        """Return True if this BuildConfig has ImageStreamTag trigger."""
        triggers = self.template['spec'].get('triggers', [])
        if not triggers:
            return False
        for trigger in triggers:
            if trigger['type'] == 'ImageChange' and \
                    trigger['imageChange']['from']['kind'] == 'ImageStreamTag':
                return True
        return False

    def has_tag_suffixes_placeholder(self):
        phase = 'postbuild_plugins'
        plugin = 'tag_from_config'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return False

        placeholder = '{{TAG_SUFFIXES}}'
        plugin_conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins', 'tag_from_config')
        return plugin_conf.get('args', {}).get('tag_suffixes') == placeholder

    def set_label(self, name, value):
        if not value:
            value = ''
        self.template['metadata'].setdefault('labels', {})
        self.template['metadata']['labels'][name] = value

    def render_reactor_config(self):
        if self.spec.reactor_config_secret.value is None:
            logger.debug("removing reactor_config plugin: no secret")
            self.dj.remove_plugin('prebuild_plugins', 'reactor_config')

    def render_orchestrate_build(self):
        phase = 'buildstep_plugins'
        plugin = 'orchestrate_build'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        if self.spec.platforms.value is None:
            logger.debug('removing %s plugin: no platforms', plugin)
            self.dj.remove_plugin(phase, plugin)
            return

        # Parameters to be used in call to create_worker_build
        build_kwargs = {
            'component': self.spec.component.value,
            'git_branch': self.spec.git_branch.value,
            'git_ref': self.spec.git_ref.value,
            'git_uri': self.spec.git_uri.value,
            'koji_task_id': self.spec.koji_task_id.value,
            'filesystem_koji_task_id': self.spec.filesystem_koji_task_id.value,
            'scratch': self.scratch,
            'target': self.spec.koji_target.value,
            'user': self.spec.user.value,
            'yum_repourls': self.spec.yum_repourls.value,
            'arrangement_version': self.spec.arrangement_version.value,
        }

        self.dj.dock_json_set_arg(phase, plugin, 'platforms', self.spec.platforms.value)
        self.dj.dock_json_set_arg(phase, plugin, 'build_kwargs', build_kwargs)

        equal_labels_string = None
        equal_labels_sets = []
        if self.spec.equal_labels.value:
            for equal_set in self.spec.equal_labels.value:
                equal_labels_sets.append(':'.join(equal_set))
            equal_labels_string = ','.join(equal_labels_sets)

        # Parameters to be used as Configuration overrides for each worker
        config_kwargs = {
            'authoritative_registry': self.spec.authoritative_registry.value,
            'distribution_scope': self.spec.distribution_scope.value,
            'info_url_format': self.spec.info_url_format.value,
            'koji_hub': self.spec.kojihub.value,
            'koji_root': self.spec.kojiroot.value,
            'openshift_required_version': sanitize_version(self._openshift_required_version),
            'pulp_registry_name': self.spec.pulp_registry.value,
            'registry_api_versions': ','.join(self.spec.registry_api_versions.value or []) or None,
            'smtp_additional_addresses': ','.join(self.spec.smtp_additional_addresses.value or [])
                                         or None,
            'smtp_email_domain': self.spec.smtp_email_domain.value,
            'smtp_error_addresses': ','.join(self.spec.smtp_error_addresses.value or []) or None,
            'smtp_from': self.spec.smtp_from.value,
            'smtp_host': self.spec.smtp_host.value,
            'smtp_to_pkgowner': self.spec.smtp_to_pkgowner.value,
            'smtp_to_submitter': self.spec.smtp_to_submitter.value,
            'source_registry_uri': self.spec.source_registry_uri.value,
            'sources_command': self.spec.sources_command.value,
            'vendor': self.spec.vendor.value,
            'equal_labels': equal_labels_string,
            'artifacts_allowed_domains': ','.join(self.spec.artifacts_allowed_domains.value or [])
                                         or None,
            'yum_proxy': self.spec.yum_proxy.value,
        }

        # Remove empty values, and always convert to string for better interaction
        # with Configuration class and JSON encoding
        config_kwargs = dict((k, str(v)) for k, v in config_kwargs.items() if v is not None)

        if not self.spec.build_imagestream.value:
            config_kwargs['build_image'] = self.spec.build_image.value

        self.dj.dock_json_set_arg(phase, plugin, 'config_kwargs', config_kwargs)

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
        if not self.dj.dock_json_has_plugin_conf('exit_plugins',
                                                 'store_metadata_in_osv3'):
            return

        self.dj.dock_json_set_arg('exit_plugins', "store_metadata_in_osv3",
                                  "url",
                                  self.spec.builder_openshift_url.value)

        if use_auth is not None:
            self.dj.dock_json_set_arg('exit_plugins',
                                      "store_metadata_in_osv3",
                                      "use_auth", use_auth)

    def set_secret_for_plugin(self, secret, plugin=None, mount_path=None):
        """
        Sets secret for plugin, if no plugin specified
        it will also set general secret

        :param secret: str, secret name
        :param plugin: tuple, (plugin type, plugin name, argument name)
        :param mount_path: str, mount path of secret
        """
        has_plugin_conf = False
        if plugin is not None:
            has_plugin_conf = self.dj.dock_json_has_plugin_conf(plugin[0],
                                                                plugin[1])
        if 'secrets' in self.template['spec']['strategy']['customStrategy']:
            if not plugin or has_plugin_conf:

                custom = self.template['spec']['strategy']['customStrategy']
                if mount_path:
                    secret_path = mount_path
                else:
                    secret_path = os.path.join(SECRETS_PATH, secret)

                logger.info("Configuring %s secret at %s", secret, secret_path)
                existing = [secret_mount for secret_mount in custom['secrets']
                            if secret_mount['secretSource']['name'] == secret]
                if existing:
                    logger.debug("secret %s already set", secret)
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
                if plugin and plugin[2] is not None:
                    self.dj.dock_json_set_arg(*(plugin + (secret_path,)))
            else:
                logger.debug("not setting secret for unused plugin %s",
                             plugin[1])

    def set_secrets(self, secrets):
        """
        :param secrets: dict, {(plugin type, plugin name, argument name): secret name}
            for example {('exit_plugins', 'koji_promote', 'koji_ssl_certs'): 'koji_ssl_certs', ...}
        """
        secret_set = False
        for (plugin, secret) in secrets.items():
            if not isinstance(plugin, tuple) or len(plugin) != 3:
                raise ValueError('got "%s" as secrets key, need 3-tuple' % plugin)
            if secret is not None:
                if isinstance(secret, list):
                    for secret_item in secret:
                        self.set_secret_for_plugin(secret_item, plugin=plugin)
                else:
                    self.set_secret_for_plugin(secret, plugin=plugin)
                secret_set = True

        if not secret_set:
            # remove references to secret if no secret was set
            if 'secrets' in self.template['spec']['strategy']['customStrategy']:
                del self.template['spec']['strategy']['customStrategy']['secrets']

    def set_kerberos_auth(self, plugins):
        if not self.spec.koji_use_kerberos.value:
            return

        krb_principal = self.spec.koji_kerberos_principal.value
        krb_keytab = self.spec.koji_kerberos_keytab.value

        if not (krb_principal and krb_keytab):
            logger.debug('Kerberos auth requested but missing principal and/or keytab values')
            return

        for phase, plugin in plugins:
            if not self.dj.dock_json_has_plugin_conf(phase, plugin):
                continue

            self.dj.dock_json_set_arg(phase, plugin, 'koji_principal', krb_principal)
            self.dj.dock_json_set_arg(phase, plugin, 'koji_keytab', krb_keytab)

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
            logger.info("removing v2-only plugins: pulp_sync, delete_from_registry")
            self.dj.remove_plugin('postbuild_plugins', 'pulp_sync')
            self.dj.remove_plugin('exit_plugins', 'delete_from_registry')

            # remove extra tag_and_push config
            self.remove_tag_and_push_registries(tag_and_push_registries, 'v2')

        # Remove 'version' from tag_and_push plugin config as it's no
        # longer needed
        for regdict in tag_and_push_registries.values():
            if 'version' in regdict:
                del regdict['version']

    def adjust_for_triggers(self):
        """Remove trigger-related plugins when needed

        If there are no triggers defined, it's assumed the
        feature is disabled and all trigger-related plugins
        are removed.

        If there are triggers defined, and this is a custom
        base image, some trigger-related plugins do not apply.
        All but import_image are disabled in this case.

        Additionally, this method ensures that custom base
        images never have triggers since triggering a base
        image rebuild is not a valid scenario.
        """
        triggers = self.template['spec'].get('triggers', [])

        remove_plugins = [
            ("prebuild_plugins", "check_and_set_rebuild"),
            ("prebuild_plugins", "stop_autorebuild_if_disabled"),
        ]

        should_remove = False
        if triggers and self.is_custom_base_image():
            msg = "removing %s from request because custom base image"
            del self.template['spec']['triggers']
            should_remove = True

        elif not triggers:
            remove_plugins.append(("postbuild_plugins", "import_image"))
            msg = "removing %s from request because there are no triggers"
            should_remove = True

        if should_remove:
            for when, which in remove_plugins:
                logger.info(msg, which)
                self.dj.remove_plugin(when, which)

    def adjust_for_scratch(self):
        """
        Remove certain plugins in order to handle the "scratch build"
        scenario. Scratch builds must not affect subsequent builds,
        and should not be imported into Koji.
        """
        if self.scratch:
            remove_plugins = [
                ("prebuild_plugins", "koji_parent"),
                ("postbuild_plugins", "compress"),  # only for Koji
                ("postbuild_plugins", "koji_upload"),
                ("postbuild_plugins", "fetch_worker_metadata"),
                ("exit_plugins", "koji_promote"),
                ("exit_plugins", "koji_import"),
                ("exit_plugins", "koji_tag_build"),
            ]

            if not self.has_tag_suffixes_placeholder():
                remove_plugins.append(("postbuild_plugins", "tag_from_config"))

            for when, which in remove_plugins:
                logger.info("removing %s from scratch build request",
                            which)
                self.dj.remove_plugin(when, which)

            if self.dj.dock_json_has_plugin_conf('postbuild_plugins',
                                                 'tag_by_labels'):
                self.dj.dock_json_set_arg('postbuild_plugins', 'tag_by_labels',
                                          'unique_tag_only', True)

    def adjust_for_custom_base_image(self):
        """
        Disable plugins to handle builds depending on whether
        or not this is a build from a custom base image.
        """
        plugins = []
        if self.is_custom_base_image():
            # Plugins irrelevant to building base images.
            plugins.append(("prebuild_plugins", "pull_base_image"))
            plugins.append(("prebuild_plugins", "koji_parent"))
            msg = "removing %s from custom image build request"

        else:
            # Plugins not needed for building non base images.
            plugins.append(("prebuild_plugins", "add_filesystem"))
            msg = "removing %s from non custom image build request"

        for when, which in plugins:
            logger.info(msg, which)
            self.dj.remove_plugin(when, which)

    def adjust_for_repo_info(self):
        if not self._repo_info:
            logger.warning('repo info not set')
            return

        if not self._repo_info.configuration.is_autorebuild_enabled():
            logger.info('autorebuild is disabled in repo configuration, removing triggers')
            self.template['spec'].pop('triggers', None)

        else:
            labels = Labels(self._repo_info.dockerfile_parser.labels)
            try:
                labels.get_name_and_value(Labels.LABEL_TYPE_RELEASE)
            except KeyError:
                # As expected, release label not set in Dockerfile
                pass
            else:
                raise RuntimeError('when autorebuild is enabled in repo configuration, '
                                   '"release" label must not be set in Dockerfile')

    def render_add_filesystem(self):
        phase = 'prebuild_plugins'
        plugin = 'add_filesystem'

        if self.dj.dock_json_has_plugin_conf(phase, plugin):
            if not self.spec.kojihub.value:
                raise OsbsValidationException(
                    'Custom base image builds require kojihub to be defined')
            self.dj.dock_json_set_arg(phase, plugin, 'koji_hub',
                                      self.spec.kojihub.value)
            if self.spec.yum_repourls.value:
                self.dj.dock_json_set_arg(phase, plugin, 'repos',
                                          self.spec.yum_repourls.value)
            if self.spec.platforms.value:
                self.dj.dock_json_set_arg(phase, plugin, 'architectures',
                                          self.spec.platforms.value)

            if self.spec.filesystem_koji_task_id.value:
                self.dj.dock_json_set_arg(phase, plugin, 'from_task_id',
                                          self.spec.filesystem_koji_task_id.value)

    def render_add_labels_in_dockerfile(self):
        phase = 'prebuild_plugins'
        plugin = 'add_labels_in_dockerfile'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        implicit_labels = {}
        label_spec = {
            'vendor': self.spec.vendor,
            'authoritative-source-url': self.spec.authoritative_registry,
            'distribution-scope': self.spec.distribution_scope,
            'release': self.spec.release,
        }

        for label, spec in label_spec.items():
            if spec.value is not None:
                implicit_labels[label] = spec.value

        self.dj.dock_json_merge_arg(phase, plugin, 'labels', implicit_labels)

        if self.spec.info_url_format.value:
            self.dj.dock_json_set_arg(phase, plugin, 'info_url_format',
                                      self.spec.info_url_format.value)

        if self.spec.equal_labels.value:
            self.dj.dock_json_set_arg(phase, plugin, 'equal_labels',
                                      self.spec.equal_labels.value)

    def render_koji(self):
        """
        if there is yum repo specified, don't pick stuff from koji
        """
        phase = 'prebuild_plugins'
        plugin = 'koji'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        if self.spec.yum_repourls.value:
            logger.info("removing koji from request "
                        "because there is yum repo specified")
            self.dj.remove_plugin(phase, plugin)
        elif not (self.spec.koji_target.value and
                  self.spec.kojiroot.value and
                  self.spec.kojihub.value):
            logger.info("removing koji from request as not specified")
            self.dj.remove_plugin(phase, plugin)
        else:
            self.dj.dock_json_set_arg(phase, plugin,
                                      "target", self.spec.koji_target.value)
            self.dj.dock_json_set_arg(phase, plugin,
                                      "root", self.spec.kojiroot.value)
            self.dj.dock_json_set_arg(phase, plugin,
                                      "hub", self.spec.kojihub.value)
            if self.spec.proxy.value:
                self.dj.dock_json_set_arg(phase, plugin,
                                          "proxy", self.spec.proxy.value)

    def render_bump_release(self):
        """
        If the bump_release plugin is present, configure it
        """
        phase = 'prebuild_plugins'
        plugin = 'bump_release'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        if self.spec.release.value:
            logger.info('removing %s from request as release already specified',
                        plugin)
            self.dj.remove_plugin(phase, plugin)
            return

        hub = self.spec.kojihub.value
        if not hub:
            logger.info('removing %s from request as koji hub not specified',
                        plugin)
            self.dj.remove_plugin(phase, plugin)
            return

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

    def render_koji_upload(self, use_auth=None):
        if not self.dj.dock_json_has_plugin_conf('postbuild_plugins', 'koji_upload'):
            return

        if self.spec.kojihub.value:
            self.dj.dock_json_set_arg('postbuild_plugins', 'koji_upload',
                                      'kojihub', self.spec.kojihub.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'koji_upload', 'url',
                                      self.spec.builder_openshift_url.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'koji_upload',
                                      'build_json_dir', self.spec.builder_build_json_dir.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'koji_upload',
                                      'koji_upload_dir', self.spec.koji_upload_dir.value)
            if use_auth is not None:
                self.dj.dock_json_set_arg('postbuild_plugins', 'koji_upload',
                                          'use_auth', use_auth)
        else:
            logger.info("removing koji_upload from request as no kojihub specified")
            self.dj.remove_plugin("postbuild_plugins", "koji_upload")

    def render_koji_import(self, use_auth=None):
        if not self.dj.dock_json_has_plugin_conf('exit_plugins', 'koji_import'):
            return

        if self.spec.kojihub.value:
            self.dj.dock_json_set_arg('exit_plugins', 'koji_import', 'url',
                                      self.spec.builder_openshift_url.value)
            self.dj.dock_json_set_arg('exit_plugins', 'koji_import',
                                      'kojihub', self.spec.kojihub.value)
            koji_target = self.spec.koji_target.value
            if koji_target is not None:
                self.dj.dock_json_set_arg('exit_plugins', 'koji_import',
                                          'target', koji_target)

            if use_auth is not None:
                self.dj.dock_json_set_arg('exit_plugins', 'koji_import',
                                          'use_auth', use_auth)

        else:
            logger.info("removing koji_import from request as no kojihub specified")
            self.dj.remove_plugin("exit_plugins", "koji_import")

    def render_koji_tag_build(self):
        phase = 'exit_plugins'
        plugin = 'koji_tag_build'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        if not self.spec.kojihub.value:
            logger.info('Removing %s because no kojihub was specified', plugin)
            self.dj.remove_plugin(phase, plugin)
            return

        if not self.spec.koji_target.value:
            logger.info('Removing %s because no koji_target was specified', plugin)
            self.dj.remove_plugin(phase, plugin)
            return

        self.dj.dock_json_set_arg(phase, plugin, 'kojihub', self.spec.kojihub.value)
        self.dj.dock_json_set_arg(phase, plugin, 'target', self.spec.koji_target.value)

    def render_koji_parent(self):
        phase = 'prebuild_plugins'
        plugin = 'koji_parent'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        if not self.spec.kojihub.value:
            logger.info('Removing %s because no kojihub was specified', plugin)
            self.dj.remove_plugin(phase, plugin)
            return

        self.dj.dock_json_set_arg(phase, plugin, 'koji_hub', self.spec.kojihub.value)

    def render_sendmail(self):
        """
        if we have smtp_host and smtp_from, configure sendmail plugin,
        else remove it
        """
        phase = 'exit_plugins'
        plugin = 'sendmail'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        if self.spec.smtp_host.value and self.spec.smtp_from.value:
            self.dj.dock_json_set_arg(phase, plugin, 'url',
                                      self.spec.builder_openshift_url.value)
            self.dj.dock_json_set_arg(phase, plugin, 'smtp_host',
                                      self.spec.smtp_host.value)
            self.dj.dock_json_set_arg(phase, plugin, 'from_address',
                                      self.spec.smtp_from.value)
        else:
            logger.info("removing sendmail from request, "
                        "requires smtp_host and smtp_from")
            self.dj.remove_plugin(phase, plugin)
            return

        if self.spec.kojihub.value and self.spec.kojiroot.value:
            self.dj.dock_json_set_arg(phase, plugin,
                                      'koji_hub', self.spec.kojihub.value)
            self.dj.dock_json_set_arg(phase, plugin,
                                      "koji_root", self.spec.kojiroot.value)

            if self.spec.smtp_to_submitter.value:
                self.dj.dock_json_set_arg(phase, plugin, 'to_koji_submitter',
                                          self.spec.smtp_to_submitter.value)
            if self.spec.smtp_to_pkgowner.value:
                self.dj.dock_json_set_arg(phase, plugin, 'to_koji_pkgowner',
                                          self.spec.smtp_to_pkgowner.value)

        if self.spec.smtp_additional_addresses.value:
            self.dj.dock_json_set_arg(phase, plugin, 'additional_addresses',
                                      self.spec.smtp_additional_addresses.value)

        if self.spec.smtp_error_addresses.value:
            self.dj.dock_json_set_arg(phase, plugin,
                                      'error_addresses', self.spec.smtp_error_addresses.value)

        if self.spec.smtp_email_domain.value:
            self.dj.dock_json_set_arg(phase, plugin,
                                      'email_domain', self.spec.smtp_email_domain.value)

    def render_fetch_maven_artifacts(self):
        """Configure fetch_maven_artifacts plugin"""
        phase = 'prebuild_plugins'
        plugin = 'fetch_maven_artifacts'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        koji_hub = self.spec.kojihub.value
        koji_root = self.spec.kojiroot.value

        if not koji_hub and not koji_root:
            logger.info('Removing %s because kojihub and kojiroot were not specified', plugin)
            self.dj.remove_plugin(phase, plugin)
            return

        self.dj.dock_json_set_arg(phase, plugin, 'koji_hub', koji_hub)
        self.dj.dock_json_set_arg(phase, plugin, "koji_root", koji_root)

        if self.spec.artifacts_allowed_domains.value:
            self.dj.dock_json_set_arg(phase, plugin, 'allowed_domains',
                                      self.spec.artifacts_allowed_domains.value)

    def render_tag_from_config(self):
        """Configure tag_from_config plugin"""
        phase = 'postbuild_plugins'
        plugin = 'tag_from_config'
        if not self.has_tag_suffixes_placeholder():
            return

        unique_tag = self.spec.image_tag.value.split(':')[-1]
        tag_suffixes = {'unique': [unique_tag], 'primary': []}

        if self.spec.build_type.value == BUILD_TYPE_ORCHESTRATOR and not self.scratch:
            tag_suffixes['primary'].extend(['latest', '{version}', '{version}-{release}'])
            tag_suffixes['primary'].extend(self._repo_info.additional_tags.tags)

        self.dj.dock_json_set_arg(phase, plugin, 'tag_suffixes', tag_suffixes)

    def render_pulp_pull(self):
        """
        If a pulp registry is specified, use pulp_pull plugin
        """
        phase = 'postbuild_plugins'
        plugin = 'pulp_pull'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        pulp_registry = self.spec.pulp_registry.value
        if not pulp_registry:
            logger.info("removing %s from request, requires pulp_registry", pulp_registry)
            self.dj.remove_plugin(phase, plugin)

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
        If a pulp registry is specified, use the pulp plugin as well as the
        delete_from_registry to delete the image after sync
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
                self.set_secret_for_plugin(registry_secret,
                                           plugin=('postbuild_plugins',
                                                   'pulp_sync',
                                                   'registry_secret_path'))

            # Verify we have a pulp secret
            if self.spec.pulp_secret.value is None:
                raise OsbsValidationException("Pulp registry specified "
                                              "but no auth config")

            source_registry = self.spec.source_registry_uri.value
            perform_delete = (source_registry is None or
                              source_registry.docker_uri != registry.docker_uri)
            if perform_delete:
                push_conf = self.dj.dock_json_get_plugin_conf('exit_plugins',
                                                              'delete_from_registry')
                args = push_conf.setdefault('args', {})
                delete_registries = args.setdefault('registries', {})
                placeholder = '{{REGISTRY_URI}}'

                # use passed in params like 'insecure' if available
                if placeholder in delete_registries:
                    regdict = delete_registries[placeholder].copy()
                    del delete_registries[placeholder]
                else:
                    regdict = {}

                if registry_secret:
                    regdict['secret'] = \
                        os.path.join(SECRETS_PATH, registry_secret)
                    # tag_and_push configured the registry secret, no neet to set it again

                delete_registries[docker_registry] = regdict

                self.dj.dock_json_set_arg('exit_plugins', 'delete_from_registry',
                                          'registries', delete_registries)
            else:
                logger.info("removing delete_from_registry from request, "
                            "source and target registry are identical")
                self.dj.remove_plugin("exit_plugins", "delete_from_registry")
        else:
            # If no pulp registry is specified, don't run the pulp plugin
            logger.info("removing pulp_sync+delete_from_registry from request, "
                        "requires pulp_registry and a v2 registry")
            self.dj.remove_plugin("postbuild_plugins", "pulp_sync")
            self.dj.remove_plugin("exit_plugins", "delete_from_registry")

    def render_group_manifests(self):
        """
        Configure the group_manifests plugin. Group is always set to false for now.
        """
        if not self.dj.dock_json_has_plugin_conf('postbuild_plugins',
                                                 'group_manifests'):
            return

        pulp_registry = self.spec.pulp_registry.value
        if pulp_registry:
            self.dj.dock_json_set_arg('postbuild_plugins', 'group_manifests',
                                      'pulp_registry_name', pulp_registry)

            # Verify we have either a secret or username/password
            if self.spec.pulp_secret.value is None:
                conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins',
                                                         'group_manifests')
                args = conf.get('args', {})
                if 'username' not in args:
                    raise OsbsValidationException("Pulp registry specified "
                                                  "but no auth config")

            self.dj.dock_json_set_arg('postbuild_plugins', 'group_manifests',
                                      'group', False)
            goarch = {}
            for platform in self.platform_descriptors:
                goarch[platform] = self.platform_descriptors[platform]['architecture']
            self.dj.dock_json_set_arg('postbuild_plugins', 'group_manifests',
                                      'goarch', goarch)

        else:
            # If no pulp registry is specified, don't run the pulp plugin
            logger.info("removing group_manifests from request, "
                        "requires pulp_registry")
            self.dj.remove_plugin("postbuild_plugins", "group_manifests")

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

    def render_distgit_fetch_artefacts(self):
        phase = 'prebuild_plugins'
        plugin = 'distgit_fetch_artefacts'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return

        if self.spec.sources_command.value is not None:
            self.dj.dock_json_set_arg(phase, plugin, "command",
                                      self.spec.sources_command.value)
        else:
            logger.info('removing {0}, no sources_command was provided'.format(plugin))
            self.dj.remove_plugin(phase, plugin)

    def render_pull_base_image(self):
        phase = 'prebuild_plugins'
        plugin = 'pull_base_image'
        if not self.dj.dock_json_has_plugin_conf(phase, plugin):
            return
        # pull_base_image wants a docker URI so strip off the scheme part
        source_registry = self.spec.source_registry_uri.value
        self.dj.dock_json_set_arg(phase, plugin, 'parent_registry',
                                  source_registry.docker_uri if source_registry else None)

    def render_customizations(self):
        """
        Customize prod_inner for site specific customizations
        """

        disable_plugins = self.customize_conf.get('disable_plugins', [])
        if not disable_plugins:
            logger.debug("No site-specific plugins to disable")
        else:
            for plugin_dict in disable_plugins:
                try:
                    self.dj.remove_plugin(
                        plugin_dict['plugin_type'],
                        plugin_dict['plugin_name']
                    )
                    logger.debug(
                        "site-specific plugin disabled -> Type:{0} Name:{1}".format(
                            plugin_dict['plugin_type'],
                            plugin_dict['plugin_name']
                        )
                    )
                except KeyError:
                    # Malformed config
                    logger.debug("Invalid custom configuration found for disable_plugins")

        enable_plugins = self.customize_conf.get('enable_plugins', [])
        if not enable_plugins:
            logger.debug("No site-specific plugins to enable")
        else:
            for plugin_dict in enable_plugins:
                try:
                    self.dj.add_plugin(
                        plugin_dict['plugin_type'],
                        plugin_dict['plugin_name'],
                        plugin_dict['plugin_args']
                    )
                    logger.debug(
                        "site-specific plugin enabled -> Type:{0} Name:{1} Args: {2}".format(
                            plugin_dict['plugin_type'],
                            plugin_dict['plugin_name'],
                            plugin_dict['plugin_args']
                        )
                    )
                except KeyError:
                    # Malformed config
                    logger.debug("Invalid custom configuration found for enable_plugins")

    def render_version(self):
        self.dj.dock_json_set_param('client_version', client_version)

    def render_name(self):
        """Sets the Build/BuildConfig object name"""
        name = self.spec.name.value

        if self.scratch:
            name = self.spec.image_tag.value
            platform = self.spec.platform.value
            # Platform name may contain characters not allowed by OpenShift.
            if platform:
                platform_suffix = '-{0}'.format(platform)
                if name.endswith(platform_suffix):
                    name = name[:-len(platform_suffix)]

            _, salt, timestamp = name.rsplit('-', 2)

            name = 'scratch-{0}-{1}'.format(salt, timestamp)

        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        self.template['metadata']['name'] = name

    def render_node_selectors(self):
        # for worker builds set nodeselectors
        if self.spec.platforms.value is None:

            # auto or explicit build selector
            if self.is_auto:
                self.template['spec']['nodeSelector'] = self.auto_build_node_selector
            # scratch build nodeselector
            elif self.scratch:
                self.template['spec']['nodeSelector'] = self.scratch_build_node_selector
            # explicit build nodeselector
            else:
                self.template['spec']['nodeSelector'] = self.explicit_build_node_selector

            # platform nodeselector
            if self.platform_node_selector:
                self.template['spec']['nodeSelector'].update(self.platform_node_selector)

    def render(self, validate=True):
        if validate:
            self.spec.validate()

        self.render_customizations()
        self.render_name()
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

        if self.has_ist_trigger():
            imagechange = self.template['spec']['triggers'][0]['imageChange']
            imagechange['from']['name'] = self.spec.trigger_imagestreamtag.value

        self.render_add_yum_repo_by_url()

        use_auth = self.spec.use_auth.value
        self.render_check_and_set_rebuild(use_auth=use_auth)
        self.render_store_metadata_in_osv3(use_auth=use_auth)

        # Remove legacy sourceSecret in case an older template is used.
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
        self.set_label('git-branch', self.spec.git_branch.value)

        self.render_distgit_fetch_artefacts()
        self.render_pull_base_image()

        self.adjust_for_repo_info()
        self.adjust_for_triggers()
        self.adjust_for_scratch()
        self.adjust_for_custom_base_image()

        # Enable/disable plugins as needed for target registry API versions
        self.adjust_for_registry_api_versions()

        self.set_secrets({('prebuild_plugins',
                           'reactor_config',
                           'config_path'):
                          self.spec.reactor_config_secret.value,

                          ('postbuild_plugins',
                           'pulp_push',
                           'pulp_secret_path'):
                          self.spec.pulp_secret.value,

                          ('postbuild_plugins',
                           'pulp_sync',
                           'pulp_secret_path'):
                          self.spec.pulp_secret.value,

                          # pulp_sync registry_secret_path set
                          # in render_pulp_sync

                          ('exit_plugins', 'koji_promote', 'koji_ssl_certs'):
                          self.spec.koji_certs_secret.value,

                          ('exit_plugins', 'koji_import', 'koji_ssl_certs'):
                          self.spec.koji_certs_secret.value,

                          ('postbuild_plugins', 'koji_upload', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('exit_plugins', 'koji_tag_build', 'koji_ssl_certs'):
                          self.spec.koji_certs_secret.value,

                          ('prebuild_plugins', 'add_filesystem', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('prebuild_plugins', 'bump_release', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('prebuild_plugins', 'koji', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('prebuild_plugins', 'koji_parent', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('prebuild_plugins', 'fetch_maven_artifacts', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('exit_plugins', 'sendmail', 'koji_ssl_certs_dir'):
                          self.spec.koji_certs_secret.value,

                          ('postbuild_plugins', 'tag_and_push',
                           # Only set the secrets for the build, don't
                           # add the path to the plugin's
                           # configuration. This is done elsewhere.
                           None):
                          self.spec.registry_secrets.value,

                          ('buildstep_plugins', 'orchestrate_build', 'osbs_client_config'):
                          self.spec.client_config_secret.value})

        self.set_kerberos_auth([
            ('prebuild_plugins', 'fetch_maven_artifacts'),
            ('postbuild_plugins', 'koji_upload'),
            ('exit_plugins', 'koji_promote'),
            ('exit_plugins', 'koji_import'),
            ('exit_plugins', 'koji_tag_build'),
            ('exit_plugins', 'sendmail')
        ])

        for (secret, path) in self.spec.token_secrets.value.items():
            self.set_secret_for_plugin(secret, mount_path=path)

        if self.spec.pulp_secret.value:
            # Don't push to docker registry, we're using pulp here
            # but still construct the unique tag
            self.template['spec']['output']['to']['name'] = \
                self.spec.image_tag.value

        koji_task_id = self.spec.koji_task_id.value
        if koji_task_id is not None:
            self.set_label('koji-task-id', str(koji_task_id))

        use_auth = self.spec.use_auth.value
        self.render_reactor_config()
        self.render_orchestrate_build()
        self.render_add_filesystem()
        self.render_add_labels_in_dockerfile()
        self.render_koji()
        self.render_bump_release()
        self.render_koji_parent()
        self.render_import_image(use_auth=use_auth)
        self.render_pulp_pull()
        self.render_pulp_push()
        self.render_pulp_sync()
        self.render_group_manifests()
        self.render_koji_promote(use_auth=use_auth)
        self.render_koji_upload(use_auth=use_auth)
        self.render_koji_import(use_auth=use_auth)
        self.render_koji_tag_build()
        self.render_sendmail()
        self.render_fetch_maven_artifacts()
        self.render_tag_from_config()
        self.render_version()
        self.render_node_selectors()

        self.dj.write_dock_json()
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json
