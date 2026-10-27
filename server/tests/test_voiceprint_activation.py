"""Retired admission has no public or internal management routes."""
from fastapi import APIRouter
from eidolon_admin_server.app.management.voiceprints_router import router
from eidolon_admin_server.local_api.management.voiceprints import register_voiceprint_routes

def test_admission_routes_are_removed():
    public = APIRouter()
    register_voiceprint_routes(public, None, None, None)
    for routes in (router.routes, public.routes):
        paths = [r.path for r in routes]
        assert paths
        assert all("/companions/" not in p and "/policy" not in p and "/bindings" not in p for p in paths)

        assert all(not p.endswith("/profiles/{profile_id}/person") for p in paths)
