Contribution Guide
==================

Licensing
---------

This project is licensed using the [BSD-3-Clause license](https://github.com/projectatomic/osbs-client/blob/master/LICENSE). When submitting pull requests please make sure your commit messages include a signed-off-by line. You can do this by using `git commit --signoff`.

Submitting changes
------------------

Changes are accepted through pull requests.

Please create your feature branch from the *master* branch. Make sure to add unit tests under the `tests/` subdirectory (we use py.test and flexmock for this). Tests are run automatically when you push new commits, but you can run them locally with `py.test tests` from the top directory.

Follow the PEP8 coding style.

Before a pull request is approved it must meet these criteria:
- unit tests pass
- code coverage from testing  does not decrease and new code is covered

Once it is approved by two developer team members it may be merged. To avoid creating merge commits the pull request will be rebased during the merge.
