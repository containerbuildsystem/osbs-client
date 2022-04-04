"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


from __future__ import absolute_import, unicode_literals


class Labels(object):
    """
    Provide access to a set of labels which have specific semantics

    The set of labels comes from here:
    https://github.com/projectatomic/ContainerApplicationGenericLabels

    Note that only a subset of label types (those used by OSBS) is supported:

    - LABEL_TYPE_NAME: repository name of the image
    - LABEL_TYPE_VERSION: version of the image
    - LABEL_TYPE_RELEASE: release number for this version
    - LABEL_TYPE_ARCH: architecture for the image
    - LABEL_TYPE_VENDOR: owner of the image
    - LABEL_TYPE_SOURCE: authoritative location for publishing
    - LABEL_TYPE_COMPONENT: Bugzilla (or other tracker) component
    - LABEL_TYPE_RUN: command to run the image
    - LABEL_TYPE_INSTALL: command to install the image
    - LABEL_TYPE_UNINSTALL: command to uninstall the image
    - LABEL_TYPE_OPERATOR_MANIFESTS: flags the presence of appregistry operators metadata
    - LABEL_TYPE_OPERATOR_BUNDLE_MANIFESTS: flags the presence of operators bundle metadata
    """
    LABEL_TYPE_NAME = object()
    LABEL_TYPE_VERSION = object()
    LABEL_TYPE_RELEASE = object()
    LABEL_TYPE_ARCH = object()
    LABEL_TYPE_VENDOR = object()
    LABEL_TYPE_SOURCE = object()
    LABEL_TYPE_COMPONENT = object()
    LABEL_TYPE_RUN = object()
    LABEL_TYPE_INSTALL = object()
    LABEL_TYPE_UNINSTALL = object()
    LABEL_TYPE_OPERATOR_MANIFESTS = object()
    LABEL_TYPE_OPERATOR_BUNDLE_MANIFESTS = object()
    LABEL_NAMES = {
        LABEL_TYPE_NAME: ('name', 'Name'),
        LABEL_TYPE_VERSION: ('version', 'Version'),
        LABEL_TYPE_RELEASE: ('release', 'Release'),
        LABEL_TYPE_ARCH: ('architecture', 'Architecture'),
        LABEL_TYPE_VENDOR: ('vendor', 'Vendor'),
        LABEL_TYPE_SOURCE: ('authoritative-source-url', 'Authoritative_Registry'),
        LABEL_TYPE_COMPONENT: ('com.redhat.component', 'BZComponent'),
        LABEL_TYPE_RUN: ('run', 'RUN'),
        LABEL_TYPE_INSTALL: ('install', 'INSTALL'),
        LABEL_TYPE_UNINSTALL: ('uninstall', 'UNINSTALL'),
        LABEL_TYPE_OPERATOR_MANIFESTS: ('com.redhat.delivery.appregistry',),
        LABEL_TYPE_OPERATOR_BUNDLE_MANIFESTS: ('com.redhat.delivery.operator.bundle',),
    }

    def __init__(self, df_labels):
        """
        Create a new Labels object
        providing access to actual newest labels as well as old ones
        """
        self._df_labels = df_labels
        self._label_values = {}
        for label_type, label_names in Labels.LABEL_NAMES.items():
            for lbl_name in label_names:
                if lbl_name in df_labels:
                    self._label_values[label_type] = (lbl_name, df_labels[lbl_name])
                    break

    def get_name(self, label_type):
        """
        returns the most preferred label name
        if there isn't any correct name in the list
        it will return newest label name
        """
        if label_type in self._label_values:
            return self._label_values[label_type][0]
        else:
            return Labels.LABEL_NAMES[label_type][0]

    @staticmethod
    def get_new_names_by_old():
        """Return dictionary, new label name indexed by old label name."""
        newdict = {}

        for label_type, label_names in Labels.LABEL_NAMES.items():
            for oldname in label_names[1:]:
                newdict[oldname] = Labels.LABEL_NAMES[label_type][0]
        return newdict

    def get_name_and_value(self, label_type):
        """
        Return tuple of (label name, label value)
        Raises KeyError if label doesn't exist
        """
        if label_type in self._label_values:
            return self._label_values[label_type]
        else:
            return (label_type, self._df_labels[label_type])
