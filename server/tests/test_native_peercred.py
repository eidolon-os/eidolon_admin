"""Real native Unix peer credentials and authorization on supported hosts."""
import os
import socket
import sys

import pytest

from eidolon_admin_server.lifecycle_workflow.peercred import (
    ExactUidWorkloadAuthorizer, native_peer_credentials,
)

pytestmark = pytest.mark.skipif(sys.platform not in ('linux', 'darwin'), reason='native Unix peer credentials require a supported OS')


def test_native_peer_identity_comes_from_the_connection():
    left, right = socket.socketpair()
    try:
        peer = native_peer_credentials().read(left)
        assert (peer.pid, peer.uid, peer.gid) == (os.getpid(), os.geteuid(), os.getegid())
        assert ExactUidWorkloadAuthorizer(peer.uid).authorize(peer).principal_id == 'eidolon-local-api'
        with pytest.raises(PermissionError):
            ExactUidWorkloadAuthorizer(peer.uid + 1).authorize(peer)
    finally:
        left.close()
        right.close()
