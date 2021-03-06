#!/usr/bin/env python3
# Copyright 2019 Google LLC
#
# Licensed under the the Apache License v2.0 with LLVM Exceptions (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://llvm.org/LICENSE.txt
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Interactions with Phabricator.
"""

import socket
import time
import uuid
from typing import Optional, List, Dict
from phabricator import Phabricator


class PhabTalk:
    """Talk to Phabricator to upload build results.
       See https://secure.phabricator.com/conduit/method/harbormaster.sendmessage/
    """

    def __init__(self, token: Optional[str], host: Optional[str], dryrun: bool):
        self._phab = None  # type: Optional[Phabricator]
        if not dryrun:
            self._phab = Phabricator(token=token, host=host)
            _try_call(self._phab.update_interfaces)

    @property
    def dryrun(self):
        return self._phab is None

    def get_revision_id(self, diff: str) -> Optional[str]:
        """Get the revision ID for a diff from Phabricator."""
        if self.dryrun:
            return None

        result = self._phab.differential.querydiffs(ids=[diff])
        return 'D' + result[diff]['revisionID']

    def comment_on_diff(self, diff_id: str, text: str):
        """Add a comment to a differential based on the diff_id"""
        print('Sending comment to diff {}:'.format(diff_id))
        print(text)
        revision_id = self.get_revision_id(diff_id)
        if revision_id is not None:
            self._comment_on_revision(revision_id, text)

    def _comment_on_revision(self, revision: str, text: str):
        """Add comment on a differential based on the revision id."""

        transactions = [{
            'type': 'comment',
            'value': text
        }]

        if self.dryrun:
            print('differential.revision.edit =================')
            print('Transactions: {}'.format(transactions))
            return

        # API details at
        # https://secure.phabricator.com/conduit/method/differential.revision.edit/
        self._phab.differential.revision.edit(objectIdentifier=revision,
                                              transactions=transactions)
        print('Uploaded comment to Revision D{}:{}'.format(revision, text))

    def update_build_status(self, diff_id: str, phid: str, working: bool, success: bool, lint: {} = {}, unit: [] = []):
        """Submit collected report to Phabricator.
        """

        result_type = 'pass'
        if working:
            result_type = 'working'
        elif not success:
            result_type = 'fail'

        # Group lint messages by file and line.
        lint_messages = []
        for v in lint.values():
            path = ''
            line = 0
            descriptions = []
            for e in v:
                path = e['path']
                line = e['line']
                descriptions.append('{}: {}'.format(e['name'], e['description']))
            lint_message = {
                'name': 'Pre-merge checks',
                'severity': 'warning',
                'code': 'llvm-premerge-checks',
                'path': path,
                'line': line,
                'description': '\n'.join(descriptions),
            }
            lint_messages.append(lint_message)

        if self.dryrun:
            print('harbormaster.sendmessage =================')
            print('type: {}'.format(result_type))
            print('unit: {}'.format(unit))
            print('lint: {}'.format(lint_messages))
            return

        _try_call(lambda: self._phab.harbormaster.sendmessage(
            buildTargetPHID=phid,
            type=result_type,
            unit=unit,
            lint=lint_messages))
        print('Uploaded build status {}, {} test results and {} lint results'.format(
            result_type, len(unit), len(lint_messages)))

    # TODO: deprecate
    def add_artifact(self, phid: str, file: str, name: str, results_url: str):
        artifactKey = str(uuid.uuid4())
        artifactType = 'uri'
        artifactData = {'uri': '{}/{}'.format(results_url, file),
                        'ui.external': True,
                        'name': name}
        self.create_artifact(phid, artifactKey, artifactType, artifactData)
        print('Created artifact "{}"'.format(name))

    def create_artifact(self, phid, artifact_key, artifact_type, artifact_data):
        if self.dryrun:
            print('harbormaster.createartifact =================')
            print('artifactKey: {}'.format(artifact_key))
            print('artifactType: {}'.format(artifact_type))
            print('artifactData: {}'.format(artifact_data))
            return
        _try_call(lambda: self._phab.harbormaster.createartifact(
            buildTargetPHID=phid,
            artifactKey=artifact_key,
            artifactType=artifact_type,
            artifactData=artifact_data))


class Step:
    def __init__(self):
        self.name = ''
        self.success = True
        self.duration = 0.0
        self.messages = []
        self.reproduce_commands = []

    def set_status_from_exit_code(self, exit_code: int):
        if exit_code != 0:
            self.success = False


class Report:
    def __init__(self):
        self.os = ''
        self.name = ''
        self.comments = []
        self.success = True
        self.working = False
        self.unit = []  # type: List
        self.lint = {}
        self.test_stats = {
            'pass': 0,
            'fail': 0,
            'skip': 0
        }  # type: Dict[str, int]
        self.steps = []  # type: List[Step]
        self.artifacts = []  # type: List

    def __str__(self):
        return str(self.__dict__)

    def add_lint(self, m):
        key = '{}:{}'.format(m['path'], m['line'])
        if key not in self.lint:
            self.lint[key] = []
        self.lint[key].append(m)

    def add_artifact(self, dir: str, file: str, name: str):
        self.artifacts.append({'dir': dir, 'file': file, 'name': name})


def _try_call(call):
    """Tries to call function several times retrying on socked.timeout."""
    c = 0
    while True:
        try:
            call()
        except socket.timeout as e:
            c += 1
            if c > 5:
                print('Connection to Pharicator failed, giving up: {}'.format(e))
                raise
            print('Connection to Pharicator failed, retrying: {}'.format(e))
            time.sleep(c * 10)
        break
