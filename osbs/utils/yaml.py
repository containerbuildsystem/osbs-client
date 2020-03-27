"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


from __future__ import absolute_import, unicode_literals

from pkg_resources import resource_stream
from osbs.exceptions import OsbsValidationException

import codecs
import json
import jsonschema
import logging
import yaml


logger = logging.getLogger(__name__)


def read_yaml_from_file_path(file_path, schema, package=None):
    """
    :param yaml_data: string, yaml content
    :param schema: string, file path to the JSON schema
    :package: string, package name containing the schema
    """
    with open(file_path) as f:
        yaml_data = f.read()
    return read_yaml(yaml_data, schema, package)


def read_yaml(yaml_data, schema, package=None):
    """
    :param yaml_data: string, yaml content
    :param schema: string, file path to the JSON schema
    :package: string, package name containing the schema
    """
    package = package or 'osbs'
    try:
        resource = resource_stream(package, schema)
        schema = codecs.getreader('utf-8')(resource)
    except (ImportError):
        logger.error('Unable to find package %s', package)
        raise
    except (IOError, TypeError):
        logger.error('unable to extract JSON schema, cannot validate')
        raise

    try:
        schema = json.load(schema)
    except ValueError:
        logger.error('unable to decode JSON schema, cannot validate')
        raise
    data = yaml.safe_load(yaml_data)
    validator = jsonschema.Draft4Validator(schema=schema)
    try:
        jsonschema.Draft4Validator.check_schema(schema)
        validator.validate(data)
    except jsonschema.SchemaError:
        logger.error('invalid schema, cannot validate')
        raise
    except jsonschema.ValidationError as exc:
        logger.debug("schema validation error: %s", exc)

        exc_message = get_error_message(exc)

        for error in validator.iter_errors(data):
            error_message = get_error_message(error)

            logger.debug("validation error: %s", error_message)

        raise OsbsValidationException(exc_message)

    return data


def get_error_message(error):
    path = "".join(
        ('[{}]' if isinstance(element, int) else '.{}').format(element)
        for element in error.path
    )

    # get all context messages without duplicates caused by 'anyOf' validator
    error_contexts = set()
    for context in error.context:
        error_contexts.add(context.message)

    error_message = "{}: validating '{}' has failed ({})".format(
                    path or 'at top level', error.validator,
                    ", ".join(error_contexts) or error.message)

    return error_message
