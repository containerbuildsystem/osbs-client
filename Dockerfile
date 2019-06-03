FROM fedora:24

ARG REACTOR_SOURCE="distribution"
ARG REACTOR_SOURCE_BRANCH="master"
ARG REACTOR_SOURCE_REPOSITORY="https://github.com/containerbuildsystem/atomic-reactor"
ARG OSBS_CLIENT_SOURCE_REPOSITORY="https://github.com/containerbuildsystem/osbs-client"
ARG OSBS_CLIENT_SOURCE_BRANCH="master"

RUN set -ex ; \
    dnf -y install git koji && \
    if [ "$REACTOR_SOURCE" = distribution ]; then \
      dnf -y install atomic-reactor python-atomic-reactor* osbs-client ; \
    elif [ "$REACTOR_SOURCE" = pypi ]; then \
      dnf -y install python2-pip gcc python-devel redhat-rpm-config xz-devel && \
      pip2 install atomic-reactor osbs-client ; \
    elif [ "$REACTOR_SOURCE" = git ]; then \
      dnf -y install python-pip gcc python2-devel redhat-rpm-config xz-devel && \
      cd / && \
      git clone -b ${REACTOR_SOURCE_BRANCH} --depth 1 ${REACTOR_SOURCE_REPOSITORY} && \
      cd atomic-reactor && \
      pip2 install -r ./requirements.txt && \
      python2 ./setup.py build && \
      python2 ./setup.py install && \
      cd / && \
      git clone -b ${REACTOR_SOURCE_BRANCH} --depth 1 ${OSBS_CLIENT_SOURCE_REPOSITORY} && \
      cd osbs-client && \
      pip2 install -r ./requirements.txt && \
      python2 ./setup.py build && \
      python2 ./setup.py install ; \
      rm -rf /atomic-reactor /osbs-client ; \
    fi

CMD ["atomic-reactor", "--verbose", "inside-build"]
