import json
from rest_framework import renderers

class JoseRenderer(renderers.BaseRenderer):
    """
    Renderer for RFC 7515 / RFC 7516 tokens (application/jose+json or text/plain).
    """
    media_type = 'application/jose+json'
    format = 'jose'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, str):
            # If string is already JSON-wrapped or raw compact token
            if (data.startswith('"') and data.endswith('"')) or (data.startswith('{') and data.endswith('}')):
                return data.encode('utf-8')
            return f'"{data}"'.encode('utf-8')
        return json.dumps(data).encode('utf-8')
