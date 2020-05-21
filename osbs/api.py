"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import

from collections import namedtuple
import json
import logging
import os
import os.path
import stat
import sys
import warnings
import getpass
from functools import wraps
from contextlib import contextmanager
from types import GeneratorType

from osbs.build.build_requestv2 import (
    BaseBuildRequest,
    BuildRequestV2,
    SourceBuildRequest,
)
from osbs.build.user_params import (
    load_user_params_from_json,
    BuildUserParams,
    SourceContainerUserParams
)
from osbs.build.plugins_configuration import (
    PluginsConfiguration,
    SourceContainerPluginsConfiguration,
)
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.build.config_map_response import ConfigMapResponse
from osbs.constants import (BUILD_RUNNING_STATES, WORKER_OUTER_TEMPLATE,
                            WORKER_INNER_TEMPLATE, WORKER_CUSTOMIZE_CONF,
                            ORCHESTRATOR_OUTER_TEMPLATE, ORCHESTRATOR_INNER_TEMPLATE,
                            ORCHESTRATOR_CUSTOMIZE_CONF, BUILD_TYPE_WORKER,
                            BUILD_TYPE_ORCHESTRATOR, BUILD_FINISHED_STATES,
                            DEFAULT_ARRANGEMENT_VERSION, REACTOR_CONFIG_ARRANGEMENT_VERSION,
                            FILTER_KEY, RELEASE_LABEL_FORMAT,
                            ORCHESTRATOR_SOURCES_OUTER_TEMPLATE,
                            USER_PARAMS_KIND_IMAGE_BUILDS,
                            USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS,
                            )
from osbs.core import Openshift
from osbs.exceptions import (OsbsException, OsbsValidationException, OsbsResponseException,
                             OsbsOrchestratorNotEnabled)
from osbs.utils.labels import Labels
# import utils in this way, so that we can mock standalone functions with flexmock
from osbs import utils
from osbs.utils import (retry_on_conflict, graceful_chain_get, RegistryURI, ImageName)

from six.moves import http_client, input


# Decorator for API methods.
def osbsapi(func):
    @wraps(func)
    def catch_exceptions(*args, **kwargs):
        # XXX: remove this in the future
        if kwargs.pop("namespace", None):
            warnings.warn("OSBS.%s: the 'namespace' argument is no longer supported" %
                          func.__name__)
        try:
            return func(*args, **kwargs)
        except OsbsException:
            # Re-raise OsbsExceptions
            raise
        except Exception as ex:
            # Propogate flexmock errors immediately (used in test cases)
            if getattr(ex, '__module__', None) == 'flexmock':
                raise

            # Convert anything else to OsbsException

            # Python 3 has implicit exception chaining and enhanced
            # reporting, so you get the original traceback as well as
            # the one originating here.
            # For Python 2, let's do that explicitly.
            raise OsbsException(cause=ex, traceback=sys.exc_info()[2])

    return catch_exceptions


_REQUIRED_PARAM = object()

logger = logging.getLogger(__name__)

LogEntry = namedtuple('LogEntry', ['platform', 'line'])


def validate_arrangement_version(arrangement_version):
    """Validate if the arrangement_version is supported

    Shows a warning when version is deprecated

    :param int|None arrangement_version: version to be validated
    :raises ValueError: when version is not supported
    """
    if arrangement_version is None:
        return

    if arrangement_version <= 5:
        raise ValueError('arrangement_version <= 5 is no longer supported')


class OSBS(object):
    """
    Note: all API methods return osbs.http.Response object. This is, due to historical
    reasons, untrue for list_builds and get_user, which return list of BuildResponse objects
    and dict respectively.
    """

    _GIT_LABEL_KEYS = ('git-repo-name', 'git-branch', 'git-full-repo')
    _OLD_LABEL_KEYS = ('git-repo-name', 'git-branch')

    @osbsapi
    def __init__(self, openshift_configuration, build_configuration):
        """ """
        self.os_conf = openshift_configuration
        self.build_conf = build_configuration
        self.os = Openshift(openshift_api_url=self.os_conf.get_openshift_api_uri(),
                            openshift_oauth_url=self.os_conf.get_openshift_oauth_api_uri(),
                            k8s_api_url=self.os_conf.get_k8s_api_uri(),
                            verbose=self.os_conf.get_verbosity(),
                            username=self.os_conf.get_username(),
                            password=self.os_conf.get_password(),
                            use_kerberos=self.os_conf.get_use_kerberos(),
                            client_cert=self.os_conf.get_client_cert(),
                            client_key=self.os_conf.get_client_key(),
                            kerberos_keytab=self.os_conf.get_kerberos_keytab(),
                            kerberos_principal=self.os_conf.get_kerberos_principal(),
                            kerberos_ccache=self.os_conf.get_kerberos_ccache(),
                            use_auth=self.os_conf.get_use_auth(),
                            verify_ssl=self.os_conf.get_verify_ssl(),
                            token=self.os_conf.get_oauth2_token(),
                            namespace=self.os_conf.get_namespace())
        self._bm = None

    @osbsapi
    def list_builds(self, field_selector=None, koji_task_id=None, running=None,
                    labels=None):
        """
        List builds with matching fields

        :param field_selector: str, field selector for Builds
        :param koji_task_id: str, only list builds for Koji Task ID
        :return: BuildResponse list
        """

        if running:
            running_fs = ",".join(["status!={status}".format(status=status.capitalize())
                                  for status in BUILD_FINISHED_STATES])
            if not field_selector:
                field_selector = running_fs
            else:
                field_selector = ','.join([field_selector, running_fs])
        response = self.os.list_builds(field_selector=field_selector,
                                       koji_task_id=koji_task_id, labels=labels)
        serialized_response = response.json()
        build_list = []
        for build in serialized_response["items"]:
            build_list.append(BuildResponse(build, self))

        return build_list

    def watch_builds(self, field_selector=None):
        kwargs = {}
        if field_selector is not None:
            kwargs['fieldSelector'] = field_selector

        for changetype, obj in self.os.watch_resource("builds", **kwargs):
            yield changetype, obj

    @osbsapi
    def get_build(self, build_id):
        response = self.os.get_build(build_id)
        build_response = BuildResponse(response.json(), self)
        return build_response

    @osbsapi
    def cancel_build(self, build_id):
        response = self.os.cancel_build(build_id)
        build_response = BuildResponse(response.json(), self)
        return build_response

    @osbsapi
    def get_pod_for_build(self, build_id):
        """
        :return: PodResponse object for pod relating to the build
        """
        pods = self.os.list_pods(label='openshift.io/build.name=%s' % build_id)
        serialized_response = pods.json()
        pod_list = [PodResponse(pod) for pod in serialized_response["items"]]
        if not pod_list:
            raise OsbsException("No pod for build")
        elif len(pod_list) != 1:
            raise OsbsException("Only one pod expected but %d returned" % len(pod_list))
        return pod_list[0]

    def _set_build_request_resource_limits(self, build_request):
        """Apply configured resource limits to build_request"""
        assert isinstance(build_request, BaseBuildRequest)
        cpu_limit = self.build_conf.get_cpu_limit()
        memory_limit = self.build_conf.get_memory_limit()
        storage_limit = self.build_conf.get_storage_limit()
        if any(
                limit is not None
                for limit in (cpu_limit, memory_limit, storage_limit)
        ):
            build_request.set_resource_limits(cpu=cpu_limit,
                                              memory=memory_limit,
                                              storage=storage_limit)

    @osbsapi
    def get_build_request(self, inner_template=None,
                          outer_template=None, customize_conf=None,
                          arrangement_version=DEFAULT_ARRANGEMENT_VERSION,
                          user_params=None, **kwargs
                          ):
        """
        return instance of BuildRequestV2

        :param build_type: str, unused
        :param inner_template: str, name of inner template for BuildRequest
        :param outer_template: str, name of outer template for BuildRequest
        :param customize_conf: str, name of customization config for BuildRequest
        :param arrangement_version: int, value of the arrangement version

        :return: instance of BuildRequestV2
        """
        validate_arrangement_version(arrangement_version)

        build_request = BuildRequestV2(
                build_json_store=self.os_conf.get_build_json_store(),
                osbs_api=self,
                outer_template=outer_template,
                customize_conf=customize_conf,
                user_params=user_params,
        )

        self._set_build_request_resource_limits(build_request)
        return build_request

    @osbsapi
    def get_source_container_build_request(
            self, outer_template=None,
            arrangement_version=DEFAULT_ARRANGEMENT_VERSION,
            user_params=None,
    ):
        """
        return instance of SourceBuildRequest

        :param str outer_template: name of outer template for SourceBuildRequest
        :return: instance of SourceBuildRequest
        """
        validate_arrangement_version(arrangement_version)

        build_request = SourceBuildRequest(
            osbs_api=self,
            outer_template=outer_template,
            user_params=user_params,
        )

        self._set_build_request_resource_limits(build_request)
        return build_request

    @osbsapi
    def create_build_from_buildrequest(self, build_request):
        """
        render provided build_request and submit build from it

        :param build_request: instance of build.build_request.BuildRequest
        :return: instance of build.build_response.BuildResponse
        """
        build_request.set_openshift_required_version(self.os_conf.get_openshift_required_version())
        build = build_request.render()
        response = self.os.create_build(json.dumps(build))
        build_response = BuildResponse(response.json(), self)
        return build_response

    def _get_running_builds_for_build_config(self, build_config_id):
        all_builds_for_bc = self.os.list_builds(build_config_id=build_config_id).json()['items']
        running = []
        for b in all_builds_for_bc:
            br = BuildResponse(b, self)
            if br.is_pending() or br.is_running():
                running.append(br)
        return running

    def _get_not_cancelled_builds_for_koji_task(self, koji_task_id):
        all_builds_for_task = self.os.list_builds(koji_task_id=koji_task_id).json()['items']
        not_cancelled = []

        for b in all_builds_for_task:
            br = BuildResponse(b, self)
            build_labels = br.get_labels()
            if not br.is_cancelled() and build_labels['is_autorebuild'] == "false":
                not_cancelled.append(br)

        return not_cancelled

    def _verify_labels_match(self, new_build_config, existing_build_config):
        new_labels = new_build_config['metadata']['labels']
        existing_labels = existing_build_config['metadata']['labels']

        for key in self._GIT_LABEL_KEYS:
            new_label_value = new_labels.get(key)
            existing_label_value = existing_labels.get(key)

            if (existing_label_value and existing_label_value != new_label_value):
                msg = (
                    'Git labels collide with existing build config "%s". '
                    'Existing labels: %r, '
                    'New labels: %r ') % (
                       existing_build_config['metadata']['name'],
                       existing_labels,
                       new_labels)
                raise OsbsValidationException(msg)

    def _get_existing_build_config(self, build_config):
        """
        Uses the given build config to find an existing matching build config.
        Build configs are a match if:
        - metadata.labels.git-repo-name AND metadata.labels.git-branch AND
          metadata.labels.git-full-repo are equal
        OR
        - metadata.labels.git-repo-name AND metadata.labels.git-branch are equal AND
          metadata.spec.source.git.uri are equal
        OR
        - metadata.name are equal
        """

        bc_labels = build_config['metadata']['labels']
        git_labels = {
            "label_selectors": [(key, bc_labels[key]) for key in self._GIT_LABEL_KEYS]
        }
        old_labels_kwargs = {
            "label_selectors": [(key, bc_labels[key]) for key in self._OLD_LABEL_KEYS],
            "filter_key": FILTER_KEY,
            "filter_value": graceful_chain_get(build_config, *FILTER_KEY.split('.'))
        }
        name = {
            "build_config_id": build_config['metadata']['name']
        }

        queries = (
            (self.os.get_build_config_by_labels, git_labels),
            (self.os.get_build_config_by_labels_filtered, old_labels_kwargs),
            (self.os.get_build_config, name),
        )

        existing_bc = None
        for func, kwargs in queries:
            try:
                existing_bc = func(**kwargs)
                # build config found
                break
            except OsbsException as exc:
                # doesn't exist
                logger.info('Build config NOT found via %s: %s',
                            func.__name__, str(exc))
                continue

        return existing_bc

    def _verify_running_builds(self, build_config_name):
        running_builds = self._get_running_builds_for_build_config(build_config_name)
        rb_len = len(running_builds)

        if rb_len > 0:
            # report the number of simeltamous builds to detect build spam or runaway processes
            builds = ', '.join(['%s: %s' % (b.get_build_name(), b.status) for b in running_builds])
            logger.info("Multiple builds for %s running: %s", build_config_name, builds)

    def _create_scratch_build(self, build_request):
        return self._create_build_directly(build_request)

    def _create_isolated_build(self, build_request):
        return self._create_build_directly(build_request,
                                           unique=('git-repo-name', 'git-branch',
                                                   'isolated', 'isolated-release'))

    def _create_build_directly(self, build_request, unique=None):
        logger.debug(build_request)
        build_json = build_request.render()
        build_json['kind'] = 'Build'
        build_json['spec']['serviceAccount'] = 'builder'

        builder_img = build_json['spec']['strategy']['customStrategy']['from']
        build_name = builder_img['name']
        build_kind = builder_img['kind']

        kind = builder_img['kind']
        if kind == 'ImageStreamTag':
            # Only BuildConfigs get to specify an ImageStreamTag. When
            # creating Builds directly we need to specify a
            # DockerImage.
            response = self.get_image_stream_tag(builder_img['name'])
            ref = response.json()['image']['dockerImageReference']
            builder_img['kind'] = 'DockerImage'
            builder_img['name'] = ref

        build_json['metadata'].setdefault('annotations', {})
        build_json['metadata']['annotations']['from'] = json.dumps({
            'kind': build_kind,
            'name': build_name})

        if unique:
            unique_labels = {}
            for u in unique:
                unique_labels[u] = build_json['metadata']['labels'][u]
            running_builds = self.list_builds(running=True, labels=unique_labels)
            if running_builds:
                raise RuntimeError('Matching build(s) already running: {}'
                                   .format(', '.join(x.get_build_name() for x in running_builds)))

        return BuildResponse(self.os.create_build(build_json).json(), self)

    @retry_on_conflict
    def _get_or_create_imagestream(self, imagestream_name, build_request):
        insecure = False
        source_registry_uri = RegistryURI(build_request.source_registry['url']).docker_uri
        source_registry_insecure = build_request.source_registry.get('insecure', False)

        docker_image_repo = ImageName.parse(build_request.base_image)

        if not docker_image_repo.registry:
            docker_image_repo.registry = source_registry_uri
            insecure = source_registry_insecure
        else:
            if docker_image_repo.registry == source_registry_uri:
                insecure = source_registry_insecure

            else:
                allowed_registry = False

                if build_request.pull_registries:
                    for pull_reg in build_request.pull_registries:
                        if docker_image_repo.registry == RegistryURI(pull_reg['url']).docker_uri:
                            insecure = pull_reg.get('insecure', False)
                            allowed_registry = True
                            break

                if not allowed_registry:
                    raise RuntimeError('Not allowed explicitly specified registry: {}'.
                                       format(docker_image_repo.registry))

        # enclose only for source registry
        if docker_image_repo.registry == source_registry_uri and build_request.organization:
            docker_image_repo.enclose(build_request.organization)

        try:
            imagestream = self.get_image_stream(imagestream_name)
        except OsbsResponseException as x:
            if x.status_code != 404:
                raise

            logger.info('Creating ImageStream %s for %s', imagestream_name, docker_image_repo)
            imagestream = self.create_image_stream(imagestream_name)

        return imagestream, docker_image_repo.to_str(), insecure

    def _get_image_stream_info_for_build_request(self, build_request):
        """Return ImageStream, and ImageStreamTag name for base_image of build_request

        If build_request is not auto instantiated, objects are not fetched
        and None, None is returned.
        """
        image_stream_json = None
        image_stream_tag_name = None
        docker_image_repo = None
        insecure = None

        if build_request.has_ist_trigger():
            image_stream_tag_id = build_request.trigger_imagestreamtag
            image_stream_id, image_stream_tag_name = image_stream_tag_id.split(':')

            image_stream, docker_image_repo, insecure =\
                self._get_or_create_imagestream(image_stream_id, build_request)
            image_stream_json = image_stream.json()

        return image_stream_json, image_stream_tag_name, docker_image_repo, insecure

    @retry_on_conflict
    def _update_build_config_when_exist(self, build_json):
        existing_bc = self._get_existing_build_config(build_json)
        self._verify_labels_match(build_json, existing_bc)
        # Existing build config may have a different name if matched by
        # git-repo-name and git-branch labels. Continue using existing
        # build config name.
        build_config_name = existing_bc['metadata']['name']
        logger.debug('existing build config name to be used "%s"',
                     build_config_name)
        self._verify_running_builds(build_config_name)

        # Remove nodeSelector, will be set from build_json for worker build
        old_nodeselector = existing_bc['spec'].pop('nodeSelector', None)
        logger.debug("removing build config's nodeSelector %s", old_nodeselector)

        # Remove koji_task_id
        koji_task_id = utils.graceful_chain_get(existing_bc, 'metadata', 'labels',
                                                'koji-task-id')
        if koji_task_id is not None:
            logger.debug("removing koji-task-id %r", koji_task_id)
            utils.graceful_chain_del(existing_bc, 'metadata', 'labels', 'koji-task-id')

        utils.buildconfig_update(existing_bc, build_json)
        # Reset name change that may have occurred during
        # update above, since renaming is not supported.
        existing_bc['metadata']['name'] = build_config_name
        logger.debug('build config for %s already exists, updating...',
                     build_config_name)

        self.os.update_build_config(build_config_name, json.dumps(existing_bc))
        return existing_bc

    @retry_on_conflict
    def _update_build_config_with_triggers(self, build_json, triggers, is_autorebuild=False):
        existing_bc = self._get_existing_build_config(build_json)
        existing_bc['spec']['triggers'] = triggers
        build_config_name = existing_bc['metadata']['name']
        existing_bc['metadata']['labels']['is_autorebuild'] = "true" if is_autorebuild else "false"
        self.os.update_build_config(build_config_name, json.dumps(existing_bc))
        return existing_bc

    def _create_build_config_and_build(self, build_request):
        build_json = build_request.render()

        build_config_name = build_json['metadata']['name']
        logger.debug('build config to be named "%s"', build_config_name)
        original_bc = self._get_existing_build_config(build_json)

        image_stream, image_stream_tag_name, docker_image_repo, insecure = \
            self._get_image_stream_info_for_build_request(build_request)

        # Remove triggers in BuildConfig to avoid accidental
        # auto instance of Build. If defined, triggers will
        # be added to BuildConfig after ImageStreamTag object
        # is properly configured.
        triggers = build_json['spec'].pop('triggers', [])

        if original_bc:
            build_config_name = original_bc['metadata']['name']
            existing_bc = self._update_build_config_when_exist(build_json)

        else:
            logger.debug("build config for %s doesn't exist, creating...",
                         build_config_name)
            existing_bc = self.os.create_build_config(json.dumps(build_json)).json()

        tag_id = None
        if image_stream:
            changed_ist = self.ensure_image_stream_tag(image_stream,
                                                       image_stream_tag_name,
                                                       docker_image_repo,
                                                       scheduled=True,
                                                       insecure=insecure)
            logger.debug('Changed parent ImageStreamTag? %s', changed_ist)

            tag_id = '{}:{}'.format(image_stream['metadata']['name'], image_stream_tag_name)

        original_trigger = original_bc['spec']['triggers'] if original_bc else []
        if original_trigger:
            original_trigger[0]['imageChange'].pop('lastTriggeredImageID', None)

        if triggers or original_trigger:
            if triggers == original_trigger:
                logger.info("Trigger didn't change")
            else:
                logger.info("Trigger changed from : %s to %s", original_trigger, triggers)

        if triggers:
            is_autorebuild = False
            if build_request.skip_build and tag_id:
                imstreamtag = None
                try:
                    imstreamtag = self.get_image_stream_tag_with_retry(tag_id).json()
                except OsbsResponseException as exc:
                    if exc.status_code == http_client.NOT_FOUND:
                        logger.info("Imagestream tag doesn't exist yet: %s", tag_id)
                    else:
                        raise

                # when imagestream tag doesn't exist yet, we will just add the trigger,
                # without setting lastTriggeredImageID
                if imstreamtag:
                    triggers[0]['imageChange']['lastTriggeredImageID'] =\
                        imstreamtag['image']['dockerImageReference']
                is_autorebuild = True

            if build_request.triggered_after_koji_task is not None:
                is_autorebuild = True

            existing_bc = self._update_build_config_with_triggers(build_json, triggers,
                                                                  is_autorebuild)

        if build_request.skip_build:
            logger.info('Build skipped')
            return

        if image_stream and triggers:
            prev_version = existing_bc['status']['lastVersion']
            build_id = self.os.wait_for_new_build_config_instance(
                build_config_name, prev_version)
            build = BuildResponse(self.os.get_build(build_id).json(), self)
        else:
            response = self.os.start_build(build_config_name)
            build = BuildResponse(response.json(), self)

        return build

    def _check_labels(self, repo_info):
        labels = repo_info.labels

        required_missing = False
        req_labels = {}
        # required labels which needs to have explicit value (not from env variable)
        explicit_labels = [Labels.LABEL_TYPE_NAME,
                           Labels.LABEL_TYPE_COMPONENT]
        # version label isn't used here, but is required label in Dockerfile
        # and is used and required for atomic reactor
        # if we don't catch error here, it will fail in atomic reactor later
        for label in [Labels.LABEL_TYPE_NAME,
                      Labels.LABEL_TYPE_COMPONENT,
                      Labels.LABEL_TYPE_VERSION]:
            try:
                _, req_labels[label] = labels.get_name_and_value(label)

                if label in explicit_labels and not req_labels[label]:
                    required_missing = True
                    logger.error("required label doesn't have explicit value in Dockerfile : %s",
                                 labels.get_name(label))
            except KeyError:
                required_missing = True
                logger.error("required label missing from Dockerfile : %s",
                             labels.get_name(label))

        try:
            _, release_value = labels.get_name_and_value(Labels.LABEL_TYPE_RELEASE)
            if release_value and not RELEASE_LABEL_FORMAT.match(release_value):
                logger.error("release label '%s' doesn't match regex : %s", release_value,
                             RELEASE_LABEL_FORMAT.pattern)
                raise OsbsValidationException("release label doesn't have proper format")
        except KeyError:
            pass

        if required_missing:
            raise OsbsValidationException("required label missing from Dockerfile")

        # Verify the name label meets requirements.
        # It is made up of slash-separated name components.
        #
        # When pulling an image, the first component of the name
        # pulled is interpreted as a registry name if it contains a
        # '.' character, and otherwise the configured registries are
        # queried in turn.
        #
        # Due to this, a name with '.' in its initial component will
        # be awkward to pull from a registry because the registry name
        # will have to be explicitly supplied, e.g. "docker pull
        # foo.bar/baz" will fail because the "foo.bar" registry cannot
        # be contacted.
        #
        # Avoid this awkwardness by forbidding '.' in the initial
        # component of the image name.
        name_components = req_labels[Labels.LABEL_TYPE_NAME].split('/', 1)
        if '.' in name_components[0]:
            raise OsbsValidationException("initial image name component "
                                          "must not contain '.'")

        return req_labels

    # Gives flexmock something to mock
    def get_user_params(self, component=None, req_labels=None, **kwargs):
        req_labels = req_labels or {}
        user_component = component or req_labels[Labels.LABEL_TYPE_COMPONENT]
        user_params = BuildUserParams(build_json_store=self.os_conf.get_build_json_store())
        user_params.set_params(build_conf=self.build_conf,
                               component=user_component,
                               name_label=req_labels[Labels.LABEL_TYPE_NAME],
                               **kwargs)
        return user_params

    def _do_create_prod_build(self,
                              git_uri=_REQUIRED_PARAM, git_ref=_REQUIRED_PARAM,
                              git_branch=_REQUIRED_PARAM,
                              inner_template=None,
                              outer_template=None,
                              customize_conf=None,
                              build_type=None,
                              component=None,
                              flatpak=None,
                              git_commit_depth=None,
                              isolated=None,
                              koji_task_id=None,
                              target=None,
                              **kwargs):

        required_params = {"git_uri": git_uri, "git_ref": git_ref, "git_branch": git_branch}
        missing_params = []
        for param_name, param_arg in required_params.items():
            if param_arg is _REQUIRED_PARAM:
                missing_params.append(param_name)
        if missing_params:
            raise OsbsException('required parameter {} missing'.format(", ".join(missing_params)))

        if flatpak:
            if isolated:
                # Flatpak builds from a particular stream autogenerate the release
                # as <module_version>.<n>; it doesn't make sense to make a fix
                # from specific one of these autogenerated version. What an isolated
                # fix for module requires will have to be determined from experience.
                raise ValueError("Flatpak build cannot be isolated")

        if not git_branch:
            raise OsbsValidationException("required argument 'git_branch' can't be None")

        repo_info = utils.get_repo_info(git_uri, git_ref, git_branch=git_branch,
                                        depth=git_commit_depth)
        req_labels = self._check_labels(repo_info)

        user_params = self.get_user_params(base_image=repo_info.base_image,
                                           build_type=build_type,
                                           component=component,
                                           flatpak=flatpak,
                                           isolated=isolated,
                                           koji_target=target,
                                           koji_task_id=koji_task_id,
                                           req_labels=req_labels,
                                           repo_info=repo_info,
                                           **kwargs)

        build_request = self.get_build_request(inner_template=inner_template,
                                               outer_template=outer_template,
                                               customize_conf=customize_conf,
                                               user_params=user_params)
        build_request.set_openshift_required_version(self.os_conf.get_openshift_required_version())

        if isolated:
            if build_request.is_from_scratch_image():
                raise ValueError('"FROM scratch" image build cannot be isolated')

        builds_for_koji_task = []
        if koji_task_id and build_type == BUILD_TYPE_ORCHESTRATOR:
            # try to find build for koji_task which isn't canceled and use that one
            builds_for_koji_task = self._get_not_cancelled_builds_for_koji_task(koji_task_id)

        builds_count = len(builds_for_koji_task)
        if builds_count == 1:
            logger.info("found running build for koji task: %s",
                        builds_for_koji_task[0].get_build_name())
            response =\
                BuildResponse(self.os.get_build(builds_for_koji_task[0].get_build_name()).json(),
                              self)
        elif builds_count > 1:
            raise OsbsException("Multiple builds %s for koji task id %s" %
                                (builds_count, koji_task_id))
        elif build_request.scratch:
            logger.info("creating scratch build")
            response = self._create_scratch_build(build_request)
        elif build_request.isolated:
            logger.info("creating isolated build")
            response = self._create_isolated_build(build_request)
        else:
            logger.info("creating build from build_config")
            response = self._create_build_config_and_build(build_request)
        # when build is skipped
        if response is None:
            return

        logger.debug(response.json)
        return response

    @osbsapi
    def create_build(self, **kwargs):
        """
        take input args, create build request and submit the build

        :param kwargs: keyword args for build
        :return: instance of BuildRequest
        """
        return self._do_create_prod_build(**kwargs)

    @osbsapi
    def create_source_container_build(self,
                                      outer_template=None,
                                      arrangement_version=None,
                                      component=None,
                                      koji_task_id=None,
                                      target=None,
                                      **kwargs):
        """
        Take input args, create build request and submit the source image build

        :return: instance of BuildRequest
        """
        build_json_store = self.os_conf.get_build_json_store()
        user_params = SourceContainerUserParams(build_json_store=build_json_store)
        user_params.set_params(arrangement_version=arrangement_version,
                               build_conf=self.build_conf,
                               component=component,
                               koji_target=target,
                               koji_task_id=koji_task_id,
                               **kwargs)
        build_request = self.get_source_container_build_request(
            outer_template=outer_template or ORCHESTRATOR_SOURCES_OUTER_TEMPLATE,
            user_params=user_params
        )
        build_request.set_openshift_required_version(self.os_conf.get_openshift_required_version())

        error_messages = []
        if not component:
            error_messages.append("required argument 'component' can't be empty")
        if error_messages:
            raise OsbsValidationException(", ".join(error_messages))

        builds_for_koji_task = []
        if koji_task_id:
            # try to find build for koji_task which isn't canceled and use that one
            builds_for_koji_task = self._get_not_cancelled_builds_for_koji_task(koji_task_id)

        builds_count = len(builds_for_koji_task)
        if builds_count == 1:
            logger.info("found running build for koji task: %s",
                        builds_for_koji_task[0].get_build_name())
            response =\
                BuildResponse(self.os.get_build(builds_for_koji_task[0].get_build_name()).json(),
                              self)
        elif builds_count > 1:
            raise OsbsException("Multiple builds %s for koji task id %s" %
                                (builds_count, koji_task_id))
        else:
            logger.info("creating source container image build")
            response = self._create_build_directly(build_request)

        logger.debug(response.json)
        return response

    @osbsapi
    def create_worker_build(self, **kwargs):
        """
        Create a worker build

        Pass through method to create_prod_build with the following
        modifications:
            - platform param is required
            - release param is required
            - arrangement_version param is required, which is used to
              select which worker_inner:n.json template to use
            - inner template set to worker_inner:n.json if not set
            - outer template set to worker.json if not set
            - customize configuration set to worker_customize.json if not set

        :return: BuildResponse instance
        """
        missing = set()
        for required in ('platform', 'release', 'arrangement_version'):
            if not kwargs.get(required):
                missing.add(required)

        if missing:
            raise ValueError("Worker build missing required parameters: %s" %
                             missing)

        if kwargs.get('platforms'):
            raise ValueError("Worker build called with unwanted platforms param")

        arrangement_version = kwargs['arrangement_version']
        kwargs.setdefault('inner_template', WORKER_INNER_TEMPLATE.format(
            arrangement_version=arrangement_version))
        kwargs.setdefault('outer_template', WORKER_OUTER_TEMPLATE)
        kwargs.setdefault('customize_conf', WORKER_CUSTOMIZE_CONF)
        kwargs['build_type'] = BUILD_TYPE_WORKER
        try:
            return self._do_create_prod_build(**kwargs)
        except IOError as ex:
            if os.path.basename(ex.filename) == kwargs['inner_template']:
                raise OsbsValidationException("worker invalid arrangement_version %s" %
                                              arrangement_version)

            raise

    @osbsapi
    def create_orchestrator_build(self, **kwargs):
        """
        Create an orchestrator build

        Pass through method to create_prod_build with the following
        modifications:
            - platforms param is required
            - arrangement_version param may be used to select which
              orchestrator_inner:n.json template to use
            - inner template set to orchestrator_inner:n.json if not set
            - outer template set to orchestrator.json if not set
            - customize configuration set to orchestrator_customize.json if not set

        :return: BuildResponse instance
        """
        if not self.can_orchestrate():
            raise OsbsOrchestratorNotEnabled("can't create orchestrate build "
                                             "when can_orchestrate isn't enabled")
        extra = [x for x in ('platform',) if kwargs.get(x)]
        if extra:
            raise ValueError("Orchestrator build called with unwanted parameters: %s" %
                             extra)

        arrangement_version = kwargs.setdefault('arrangement_version',
                                                self.build_conf.get_arrangement_version())

        if arrangement_version < REACTOR_CONFIG_ARRANGEMENT_VERSION and not kwargs.get('platforms'):
            raise ValueError('Orchestrator build requires platforms param')

        kwargs.setdefault('inner_template', ORCHESTRATOR_INNER_TEMPLATE.format(
            arrangement_version=arrangement_version))
        kwargs.setdefault('outer_template', ORCHESTRATOR_OUTER_TEMPLATE)
        kwargs.setdefault('customize_conf', ORCHESTRATOR_CUSTOMIZE_CONF)
        kwargs['build_type'] = BUILD_TYPE_ORCHESTRATOR
        try:
            return self._do_create_prod_build(**kwargs)
        except IOError as ex:
            if os.path.basename(ex.filename) == kwargs['inner_template']:
                raise OsbsValidationException("orchestrator invalid arrangement_version %s" %
                                              arrangement_version)

            raise

    def _decode_build_logs_generator(self, logs):
        for line in logs:
            line = line.decode("utf-8").rstrip()
            yield line

    @osbsapi
    def get_build_logs(self, build_id, follow=False, build_json=None, wait_if_missing=False,
                       decode=False):
        """
        provide logs from build

        NOTE: Since atomic-reactor 1.6.25, logs are always in UTF-8, so if
        asked to decode, we assume that is the encoding in use. Otherwise, we
        return the bytes exactly as they came from the container.

        :param build_id: str
        :param follow: bool, fetch logs as they come?
        :param build_json: dict, to save one get-build query
        :param wait_if_missing: bool, if build doesn't exist, wait
        :param decode: bool, whether or not to decode logs as utf-8
        :return: None, bytes, or iterable of bytes
        """
        logs = self.os.logs(build_id, follow=follow, build_json=build_json,
                            wait_if_missing=wait_if_missing)

        if decode and isinstance(logs, GeneratorType):
            return self._decode_build_logs_generator(logs)

        # str or None returned from self.os.logs()
        if decode and logs is not None:
            logs = logs.decode("utf-8").rstrip()

        return logs

    @staticmethod
    def _parse_build_log_entry(entry):
        items = entry.split()
        if len(items) < 4:
            # This is not a valid build log entry
            return (None, entry)

        platform = items[2]
        if not platform.startswith("platform:"):
            # Line logged without using the appropriate LoggerAdapter
            return (None, entry)

        platform = platform.split(":", 1)[1]
        if platform == "-":
            return (None, entry)  # proper orchestrator build log entry

        # Anything else should be a worker build log entry, so we strip off
        # the leading 8 wrapping orchestrator log fields:
        # <date> <time> <platform> - <name> - <level> -
        plen = sum(len(items[i]) + 1  # include trailing space
                   for i in range(8))
        line = entry[plen:]
        # if the 3rd field is "platform:-", we strip it out
        items = line.split()
        if len(items) > 2 and items[2] == "platform:-":
            plen = sum(len(items[i]) + 1  # include trailing space
                       for i in range(3))
            line = "%s %s %s" % (items[0], items[1], line[plen:])
        return (platform, line)

    @osbsapi
    def get_orchestrator_build_logs(self, build_id, follow=False, wait_if_missing=False):
        """
        provide logs from orchestrator build

        :param build_id: str
        :param follow: bool, fetch logs as they come?
        :param wait_if_missing: bool, if build doesn't exist, wait
        :return: generator yielding objects with attributes 'platform' and 'line'
        """
        logs = self.get_build_logs(build_id=build_id, follow=follow,
                                   wait_if_missing=wait_if_missing, decode=True)

        if logs is None:
            return
        if isinstance(logs, GeneratorType):
            for entries in logs:
                for entry in entries.splitlines():
                    yield LogEntry(*self._parse_build_log_entry(entry))
        else:
            for entry in logs.splitlines():
                yield LogEntry(*self._parse_build_log_entry(entry))

    @osbsapi
    def wait_for_build_to_finish(self, build_id):
        response = self.os.wait_for_build_to_finish(build_id)
        build_response = BuildResponse(response, self)
        return build_response

    @osbsapi
    def wait_for_build_to_get_scheduled(self, build_id):
        response = self.os.wait_for_build_to_get_scheduled(build_id)
        build_response = BuildResponse(response, self)
        return build_response

    @osbsapi
    def update_labels_on_build(self, build_id, labels):
        response = self.os.update_labels_on_build(build_id, labels)
        return response

    @osbsapi
    def set_labels_on_build(self, build_id, labels):
        response = self.os.set_labels_on_build(build_id, labels)
        return response

    @osbsapi
    def update_labels_on_build_config(self, build_config_id, labels):
        response = self.os.update_labels_on_build_config(build_config_id, labels)
        return response

    @osbsapi
    def set_labels_on_build_config(self, build_config_id, labels):
        response = self.os.set_labels_on_build_config(build_config_id, labels)
        return response

    @osbsapi
    def update_annotations_on_build(self, build_id, annotations):
        return self.os.update_annotations_on_build(build_id, annotations)

    @osbsapi
    def set_annotations_on_build(self, build_id, annotations):
        return self.os.set_annotations_on_build(build_id, annotations)

    @osbsapi
    def import_image_tags(self, name, tags, repository, insecure=False):
        """Import image tags from specified container repository.

        :param name: str, name of ImageStream object
        :param tags: iterable, tags to be imported
        :param repository: str, remote location of container image
                                in the format <registry>/<repository>
        :param insecure: bool, indicates whenever registry is secure

        :return: bool, whether tags were imported
        """
        stream_import_file = os.path.join(self.os_conf.get_build_json_store(),
                                          'image_stream_import.json')
        with open(stream_import_file) as f:
            stream_import = json.load(f)
        return self.os.import_image_tags(name, stream_import, tags,
                                         repository, insecure)

    @osbsapi
    def get_token(self):
        if self.os.use_kerberos:
            return self.os.get_oauth_token()
        else:
            if self.os.token:
                return self.os.token

            raise OsbsValidationException("no token stored for %s" % self.os_conf.conf_section)

    @osbsapi
    def login(self, token=None, username=None, password=None):
        if self.os.use_kerberos:
            raise OsbsValidationException("can't use login when using kerberos")

        if not token:
            if username:
                self.os.username = username
            else:
                self.os.username = input("Username: ")

            if password:
                self.os.password = password
            else:
                self.os.password = getpass.getpass()
            self.os.use_auth = True
            token = self.os.get_oauth_token()

        self.os.token = token
        try:
            self.os.get_user()
        except OsbsResponseException as ex:
            if ex.status_code == http_client.UNAUTHORIZED:
                raise OsbsValidationException("token is not valid")
            raise

        token_file = utils.get_instance_token_file_name(self.os_conf.conf_section)
        token_file_dir = os.path.dirname(token_file)

        if not os.path.exists(token_file_dir):
            os.makedirs(token_file_dir)

        # Inspired by http://stackoverflow.com/a/15015748/5998718
        # For security, remove file with potentially elevated mode
        if os.path.exists(token_file):
            os.remove(token_file)

        # Open file descriptor
        fdesc = os.open(token_file,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        stat.S_IRUSR | stat.S_IWUSR)

        with os.fdopen(fdesc, 'w') as f:
            f.write(token + '\n')

    @osbsapi
    def get_user(self, username="~"):
        return self.os.get_user(username).json()

    @osbsapi
    def get_serviceaccount_tokens(self, username="~"):
        return self.os.get_serviceaccount_tokens(username)

    @osbsapi
    def get_image_stream_tag(self, tag_id):
        return self.os.get_image_stream_tag(tag_id)

    @osbsapi
    def get_image_stream_tag_with_retry(self, tag_id):
        return self.os.get_image_stream_tag_with_retry(tag_id)

    @osbsapi
    def ensure_image_stream_tag(self, stream, tag_name, docker_image_repo, scheduled=False,
                                insecure=False):
        """Ensures the tag is monitored in ImageStream

        :param stream: dict, ImageStream object
        :param tag_name: str, name of tag to check, without name of
                              ImageStream as prefix
        :param docker_image_repo: str full name of repository
        :param scheduled: bool, if True, importPolicy.scheduled will be
                                set to True in ImageStreamTag
        :return: bool, whether or not modifications were performed
        """
        img_stream_tag_file = os.path.join(self.os_conf.get_build_json_store(),
                                           'image_stream_tag.json')
        with open(img_stream_tag_file) as f:
            tag_template = json.load(f)

        return self.os.ensure_image_stream_tag(stream, tag_name, tag_template,
                                               docker_image_repo, scheduled,
                                               insecure=insecure)

    @osbsapi
    def get_image_stream(self, stream_id):
        return self.os.get_image_stream(stream_id)

    @osbsapi
    def create_image_stream(self, name):
        """
        Create an ImageStream object

        Raises exception on error

        :param name: str, name of ImageStream
        :return: response
        """
        img_stream_file = os.path.join(self.os_conf.get_build_json_store(), 'image_stream.json')
        with open(img_stream_file) as f:
            stream = json.load(f)
        stream['metadata']['name'] = name
        stream['metadata'].setdefault('annotations', {})

        return self.os.create_image_stream(json.dumps(stream))

    def _load_quota_json(self, quota_name=None):
        quota_file = os.path.join(self.os_conf.get_build_json_store(),
                                  'pause_quota.json')
        with open(quota_file) as fp:
            quota_json = json.load(fp)

        if quota_name:
            quota_json['metadata']['name'] = quota_name

        return quota_json['metadata']['name'], quota_json

    @osbsapi
    def pause_builds(self, quota_name=None):
        # First, set quota so 0 pods are allowed to be running
        quota_name, quota_json = self._load_quota_json(quota_name)
        self.os.create_resource_quota(quota_name, quota_json)

        # Now wait for running builds to finish
        while True:
            field_selector = ','.join(['status=%s' % status.capitalize()
                                       for status in BUILD_RUNNING_STATES])
            builds = self.list_builds(field_selector)

            # Double check builds are actually in running state.
            running_builds = [build for build in builds if build.is_running()]

            if not running_builds:
                break

            name = running_builds[0].get_build_name()
            logger.info("waiting for build to finish: %s", name)
            self.wait_for_build_to_finish(name)

    @osbsapi
    def resume_builds(self, quota_name=None):
        quota_name, _ = self._load_quota_json(quota_name)
        self.os.delete_resource_quota(quota_name)

    # implements subset of OpenShift's export logic in pkg/cmd/cli/cmd/exporter.go
    @staticmethod
    def _prepare_resource(resource):
        utils.graceful_chain_del(resource, 'metadata', 'resourceVersion')

    @osbsapi
    def dump_resource(self, resource_type):
        return self.os.dump_resource(resource_type).json()

    @osbsapi
    def restore_resource(self, resource_type, resources, continue_on_error=False):
        nfailed = 0
        for r in resources["items"]:
            name = utils.graceful_chain_get(r, 'metadata', 'name') or '(no name)'
            logger.debug("restoring %s/%s", resource_type, name)
            try:
                self._prepare_resource(r)
                self.os.restore_resource(resource_type, r)
            except Exception:
                if continue_on_error:
                    logger.exception("failed to restore %s/%s", resource_type, name)
                    nfailed += 1
                else:
                    raise

        if continue_on_error:
            ntotal = len(resources["items"])
            logger.info("restored %s/%s %s", ntotal - nfailed, ntotal, resource_type)

    @osbsapi
    def list_resource_quotas(self):
        return self.os.list_resource_quotas().json()

    @osbsapi
    def get_resource_quota(self, quota_name):
        return self.os.get_resource_quota(quota_name).json()

    @osbsapi
    def can_orchestrate(self):
        return self.build_conf.get_can_orchestrate()

    @osbsapi
    def create_config_map(self, name, data):
        """
        Create an ConfigMap object on the server

        Raises exception on error

        :param name: str, name of configMap
        :param data: dict, dictionary of data to be stored
        :returns: ConfigMapResponse containing the ConfigMap with name and data
        """
        config_data_file = os.path.join(self.os_conf.get_build_json_store(), 'config_map.json')
        with open(config_data_file) as f:
            config_data = json.load(f)
        config_data['metadata']['name'] = name
        data_dict = {}
        for key, value in data.items():
            data_dict[key] = json.dumps(value)
        config_data['data'] = data_dict

        response = self.os.create_config_map(config_data)
        config_map_response = ConfigMapResponse(response.json())
        return config_map_response

    @osbsapi
    def get_config_map(self, name):
        """
        Get a ConfigMap object from the server

        Raises exception on error

        :param name: str, name of configMap to get from the server
        :returns: ConfigMapResponse containing the ConfigMap with the requested name
        """
        response = self.os.get_config_map(name)
        config_map_response = ConfigMapResponse(response.json())
        return config_map_response

    @osbsapi
    def delete_config_map(self, name):
        """
        Delete a ConfigMap object from the server

        Raises exception on error

        :param name: str, name of configMap to delete from the server
        """
        self.os.delete_config_map(name)

    @contextmanager
    def retries_disabled(self):
        """
        Context manager to disable retries on requests
        :returns: OSBS object
        """
        self.os.retries_enabled = False
        yield
        self.os.retries_enabled = True

    @osbsapi
    def render_plugins_configuration(self, user_params_json):
        user_params = load_user_params_from_json(user_params_json)

        if user_params.KIND == USER_PARAMS_KIND_IMAGE_BUILDS:
            return PluginsConfiguration(user_params).render()
        elif user_params.KIND == USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS:
            return SourceContainerPluginsConfiguration(user_params).render()
        else:
            raise RuntimeError(
                "Unexpected user params kind: {}".format(user_params.KIND)
            )
