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
import yaml
from functools import wraps
from contextlib import contextmanager
from types import GeneratorType

from osbs.build.build_requestv2 import (
    BaseBuildRequest,
    BuildRequestV2,
)
from osbs.build.user_params import (
    BuildUserParams,
    SourceContainerUserParams
)
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.build.config_map_response import ConfigMapResponse
from osbs.constants import (WORKER_OUTER_TEMPLATE, WORKER_CUSTOMIZE_CONF,
                            ORCHESTRATOR_OUTER_TEMPLATE,
                            ORCHESTRATOR_CUSTOMIZE_CONF, BUILD_TYPE_WORKER,
                            BUILD_TYPE_ORCHESTRATOR, BUILD_FINISHED_STATES,
                            RELEASE_LABEL_FORMAT, VERSION_LABEL_FORBIDDEN_CHARS,
                            PRUN_TEMPLATE_USER_PARAMS, PRUN_TEMPLATE_REACTOR_CONFIG_WS,
                            PRUN_TEMPLATE_BUILD_DIR_WS)
from osbs.tekton import Openshift, PipelineRun
from osbs.exceptions import (OsbsException, OsbsValidationException, OsbsResponseException,
                             OsbsOrchestratorNotEnabled)
from osbs.utils.labels import Labels
# import utils in this way, so that we can mock standalone functions with flexmock
from osbs import utils
from osbs.utils import stringify_values

from six.moves import http_client, input


# Decorator for API methods.
def osbsapi(func):
    @wraps(func)
    def catch_exceptions(*args, **kwargs):
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


class OSBS(object):
    """
    Note: all API methods return osbs.http.Response object. This is, due to historical
    reasons, untrue for list_builds and get_user, which return list of BuildResponse objects
    and dict respectively.
    """

    _GIT_LABEL_KEYS = ('git-repo-name', 'git-branch', 'git-full-repo')
    _OLD_LABEL_KEYS = ('git-repo-name', 'git-branch')

    @osbsapi
    def __init__(self, openshift_configuration):
        """ """
        self.os_conf = openshift_configuration
        self.os = Openshift(openshift_api_url=self.os_conf.get_openshift_base_uri(),
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
        cpu_limit = self.os_conf.get_cpu_limit()
        memory_limit = self.os_conf.get_memory_limit()
        storage_limit = self.os_conf.get_storage_limit()
        if any(
                limit is not None
                for limit in (cpu_limit, memory_limit, storage_limit)
        ):
            build_request.set_resource_limits(cpu=cpu_limit,
                                              memory=memory_limit,
                                              storage=storage_limit)

    @osbsapi
    def get_build_request(self, outer_template=None, customize_conf=None,
                          user_params=None, repo_info=None, **kwargs
                          ):
        """
        return instance of BuildRequestV2

        :param build_type: str, unused
        :param outer_template: str, name of outer template for BuildRequest
        :param customize_conf: str, name of customization config for BuildRequest
        :param repo_info: RepoInfo, git repo data for the build

        :return: instance of BuildRequestV2
        """
        build_request = BuildRequestV2(
                build_json_store=self.os_conf.get_build_json_store(),
                osbs_api=self,
                outer_template=outer_template,
                customize_conf=customize_conf,
                user_params=user_params,
                repo_info=repo_info,
        )

        self._set_build_request_resource_limits(build_request)
        return build_request

    def _get_not_cancelled_builds_for_koji_task(self, koji_task_id):
        all_builds_for_task = self.os.list_builds(koji_task_id=koji_task_id).json()['items']
        not_cancelled = []

        for b in all_builds_for_task:
            br = BuildResponse(b, self)
            if not br.is_cancelled():
                not_cancelled.append(br)

        return not_cancelled

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

        try:
            _, version_value = labels.get_name_and_value(Labels.LABEL_TYPE_VERSION)
        # version doesn't exist
        except KeyError:
            pass
        else:
            if version_value:
                wrong_chars = \
                    [denied for denied in VERSION_LABEL_FORBIDDEN_CHARS if denied in version_value]

                if wrong_chars:
                    msg = "version label '{}' contains not allowed chars : '{}'".\
                        format(version_value, wrong_chars)
                    logger.error(msg)
                    raise OsbsValidationException(msg)

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
        return BuildUserParams.make_params(build_json_dir=self.os_conf.get_build_json_store(),
                                           build_conf=self.os_conf,
                                           component=user_component,
                                           name_label=req_labels[Labels.LABEL_TYPE_NAME],
                                           **kwargs)

    def _do_create_prod_build(self,
                              git_uri=_REQUIRED_PARAM, git_ref=_REQUIRED_PARAM,
                              git_branch=_REQUIRED_PARAM,
                              outer_template=None,
                              customize_conf=None,
                              build_type=None,
                              component=None,
                              flatpak=None,
                              git_commit_depth=None,
                              isolated=None,
                              koji_task_id=None,
                              target=None,
                              operator_csv_modifications_url=None,
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

        if flatpak and not repo_info.configuration.is_flatpak:
            raise OsbsException(
                "Flatpak build, "
                "but repository doesn't have a container.yaml with a flatpak: section")

        if not flatpak and repo_info.configuration.is_flatpak:
            raise OsbsException(
                "Not a flatpak build, "
                "but repository has a container.yaml with a flatpak: section")

        if operator_csv_modifications_url and not isolated:
            raise OsbsException('Only isolated build can update operator CSV metadata')

        req_labels = self._check_labels(repo_info)

        user_params = self.get_user_params(
            base_image=repo_info.base_image,
            build_type=build_type,
            component=component,
            flatpak=flatpak,
            isolated=isolated,
            koji_target=target,
            koji_task_id=koji_task_id,
            req_labels=req_labels,
            repo_info=repo_info,
            operator_csv_modifications_url=operator_csv_modifications_url,
            **kwargs)

        build_request = self.get_build_request(outer_template=outer_template,
                                               customize_conf=customize_conf,
                                               user_params=user_params,
                                               repo_info=repo_info)
        build_request.set_openshift_required_version(self.os_conf.get_openshift_required_version())

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
            logger.info("creating normal direct build")
            response = self._create_build_directly(build_request)

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

    def _get_source_container_pipeline_data(self):
        pipeline_run_postfix = utils.generate_random_postfix()
        pipeline_run_path = self.os_conf.get_pipeline_run_path()

        with open(pipeline_run_path) as f:
            yaml_data = f.read()
        pipeline_run_data = yaml.safe_load(yaml_data)

        pipeline_name = pipeline_run_data['spec']['pipelineRef']['name']
        pipeline_run_name = f'{pipeline_name}-{pipeline_run_postfix}'

        return pipeline_run_name, pipeline_run_data

    def _set_source_container_pipeline_data(self, pipeline_run_name, pipeline_run_data,
                                            user_params):
        # set pipeline run name
        pipeline_run_data['metadata']['name'] = pipeline_run_name

        # set user params
        for param in pipeline_run_data['spec']['params']:
            if param['name'] == PRUN_TEMPLATE_USER_PARAMS:
                param['value'] = user_params.to_json()

        for ws in pipeline_run_data['spec']['workspaces']:
            # set reactor config map name
            if ws['name'] == PRUN_TEMPLATE_REACTOR_CONFIG_WS:
                ws['configmap']['name'] = user_params.reactor_config_map

            # set namespace for volume claim template
            if ws['name'] == PRUN_TEMPLATE_BUILD_DIR_WS:
                ws['volumeClaimTemplate']['metadata']['namespace'] = self.os_conf.get_namespace()

        # set labels
        all_labels = {}

        if user_params.koji_task_id is not None:
            all_labels['koji-task-id'] = str(user_params.koji_task_id)

        pipeline_run_data['metadata']['labels'] = all_labels

    @osbsapi
    def create_source_container_pipeline_run(self,
                                             component=None,
                                             koji_task_id=None,
                                             target=None,
                                             **kwargs):
        """
        Take input args, create source pipeline run

        :return: instance of PiplelineRun
        """
        error_messages = []
        # most likely can be removed, source build should get component name
        # from binary build OSBS2 TBD
        if not component:
            error_messages.append("required argument 'component' can't be empty")
        if error_messages:
            raise OsbsValidationException(", ".join(error_messages))

        pipeline_run_name, pipeline_run_data = self._get_source_container_pipeline_data()

        build_json_store = self.os_conf.get_build_json_store()
        user_params = SourceContainerUserParams.make_params(
            build_json_dir=build_json_store,
            build_conf=self.os_conf,
            component=component,
            koji_target=target,
            koji_task_id=koji_task_id,
            pipeline_run_name=pipeline_run_name,
            **kwargs
        )

        self._set_source_container_pipeline_data(pipeline_run_name, pipeline_run_data, user_params)

        logger.info("creating source container image pipeline run: %s", pipeline_run_name)

        pipeline_run = PipelineRun(self.os, pipeline_run_name, pipeline_run_data)

        logger.info("pipeline run created: %s", pipeline_run.start_pipeline_run().json())

        return pipeline_run

    @osbsapi
    def create_worker_build(self, **kwargs):
        """
        Create a worker build

        Pass through method to create_prod_build with the following
        modifications:
            - platform param is required
            - release param is required
            - outer template set to worker.json if not set
            - customize configuration set to worker_customize.json if not set

        :return: BuildResponse instance
        """
        missing = set()
        for required in ('platform', 'release'):
            if not kwargs.get(required):
                missing.add(required)

        if missing:
            raise ValueError("Worker build missing required parameters: %s" %
                             missing)

        if kwargs.get('platforms'):
            raise ValueError("Worker build called with unwanted platforms param")

        kwargs.setdefault('outer_template', WORKER_OUTER_TEMPLATE)
        kwargs.setdefault('customize_conf', WORKER_CUSTOMIZE_CONF)
        kwargs['build_type'] = BUILD_TYPE_WORKER
        return self._do_create_prod_build(**kwargs)

    @osbsapi
    def create_orchestrator_build(self, **kwargs):
        """
        Create an orchestrator build

        Pass through method to create_prod_build with the following
        modifications:
            - platforms param is required
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

        kwargs.setdefault('outer_template', ORCHESTRATOR_OUTER_TEMPLATE)
        kwargs.setdefault('customize_conf', ORCHESTRATOR_CUSTOMIZE_CONF)
        kwargs['build_type'] = BUILD_TYPE_ORCHESTRATOR
        return self._do_create_prod_build(**kwargs)

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
    def update_annotations_on_build(self, build_id, annotations):
        # annotations support only string, make sure it's string
        # or json serializable object
        annotations = stringify_values(annotations)
        return self.os.update_annotations_on_build(build_id, annotations)

    @osbsapi
    def set_annotations_on_build(self, build_id, annotations):
        # annotations support only string, make sure it's string
        # or json serializable object
        annotations = stringify_values(annotations)
        return self.os.set_annotations_on_build(build_id, annotations)

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
    def get_image_stream(self, stream_id):
        return self.os.get_image_stream(stream_id)

    # implements subset of OpenShift's export logic in pkg/cmd/cli/cmd/exporter.go
    @staticmethod
    def _prepare_resource(resource):
        utils.graceful_chain_del(resource, 'metadata', 'resourceVersion')

    @osbsapi
    def list_resource_quotas(self):
        return self.os.list_resource_quotas().json()

    @osbsapi
    def get_resource_quota(self, quota_name):
        return self.os.get_resource_quota(quota_name).json()

    @osbsapi
    def can_orchestrate(self):
        return self.os_conf.get_can_orchestrate()

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
