#!/bin/bash
set -eux

# Prepare env vars
ENGINE=${ENGINE:="podman"}
OS=${OS:="centos"}
OS_VERSION=${OS_VERSION:="7"}
PYTHON_VERSION=${PYTHON_VERSION:="2"}
ACTION=${ACTION:="test"}
IMAGE="$OS:$OS_VERSION"
CONTAINER_NAME="osbs-client-$OS-$OS_VERSION-py$PYTHON_VERSION"

if [[ $ACTION == "markdownlint" ]]; then
  IMAGE="ruby"
  CONTAINER_NAME="osbs-client-$ACTION-$IMAGE"
fi

RUN="$ENGINE exec -ti $CONTAINER_NAME"

# Use arrays to prevent globbing and word splitting
engine_mounts=(-v "$PWD":"$PWD":z)
for dir in ${EXTRA_MOUNT:-}; do
  engine_mounts=("${engine_mounts[@]}" -v "$dir":"$dir":z)
done

# Create or resurrect container if needed
if [[ $($ENGINE ps -qa -f name="$CONTAINER_NAME" | wc -l) -eq 0 ]]; then
  $ENGINE run --name "$CONTAINER_NAME" -d "${engine_mounts[@]}" -w "$PWD" -ti "$IMAGE" sleep infinity
elif [[ $($ENGINE ps -q -f name="$CONTAINER_NAME" | wc -l) -eq 0 ]]; then
  echo found stopped existing container, restarting. volume mounts cannot be updated.
  $ENGINE container start "$CONTAINER_NAME"
fi

function setup_osbs() {
  # Pull fedora images from registry.fedoraproject.org
  if [[ $OS == "fedora" ]]; then
    IMAGE="registry.fedoraproject.org/$IMAGE"
  fi

  if [[ $OS == "fedora" ]]; then
    PIP_PKG="python$PYTHON_VERSION-pip"
    PIP="pip$PYTHON_VERSION"
    PKG="dnf"
    PKG_EXTRA="dnf-plugins-core"
    BUILDDEP="dnf builddep"
    PYTHON="python$PYTHON_VERSION"
  else
    PIP_PKG="python-pip"
    PIP="pip"
    PKG="yum"
    PKG_EXTRA="yum-utils epel-release"
    BUILDDEP="yum-builddep"
    PYTHON="python"
  fi



  # Install dependencies
  $RUN $PKG install -y $PKG_EXTRA
  [[ ${PYTHON_VERSION} == '3' ]] && WITH_PY3=1 || WITH_PY3=0
  $RUN $BUILDDEP --define "with_python3 ${WITH_PY3}" -y osbs-client.spec
  if [[ $OS != "fedora" ]]; then
    # Install dependecies for test, as check is disabled for rhel
    $RUN yum install -y python-flexmock python-six python-dockerfile-parse python-requests python-requests-kerberos
  fi

  # Install package
  $RUN $PKG install -y $PIP_PKG
  if [[ $PYTHON_VERSION == 3 ]]; then
    # https://fedoraproject.org/wiki/Changes/Making_sudo_pip_safe
    $RUN mkdir -p /usr/local/lib/python3.6/site-packages/
  fi

  $RUN $PIP install -U pip
  $RUN $PIP install -U setuptools
  $RUN $PYTHON setup.py install

  # Install packages for tests
  $RUN $PIP install -r tests/requirements.txt
}

case ${ACTION} in
"test")
  setup_osbs
  TEST_CMD="py.test --cov osbs --cov-report html tests"
  ;;
"pylint")
  setup_osbs
  # This can run only at fedora because pylint is not packaged in centos
  # use distro pylint to not get too new pylint version
  $RUN $PKG install -y "${PYTHON}-pylint"
  PACKAGES='osbs tests'
  TEST_CMD="${PYTHON} -m pylint ${PACKAGES}"
  ;;
"bandit")
  setup_osbs
  $RUN $PIP install bandit
  TEST_CMD="bandit-baseline -r osbs -ll -ii"
  ;;
"markdownlint")
  $RUN gem install "mdl:0.9"
  TEST_CMD="mdl -g ."
  ;;
*)
  echo "Unknown action: ${ACTION}"
  exit 2
  ;;
esac

# Run tests
$RUN  ${TEST_CMD} "$@"

echo "To run tests again:"
echo "$RUN ${TEST_CMD}"
