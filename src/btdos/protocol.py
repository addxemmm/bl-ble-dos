"""Pure SDP encoding/parsing. Malformed inputs are tested offline only."""
from .cases import make_sdp_request, parse_sdp_response

__all__ = ["make_sdp_request", "parse_sdp_response"]
