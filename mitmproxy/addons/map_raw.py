from __future__ import absolute_import, annotations

from collections.abc import (
    Generator,
    Sequence
)
from http.client import parse_headers
from logging import getLogger
from pathlib import Path
from typing import NamedTuple

from .. import ctx
from ..addonmanager import Loader
from ..exceptions import OptionsError
from ..flowfilter import (
    TFilter,
    parse as parse_flow_filter
)
from ..http import (
    HTTPFlow,
    Request,
    Response
)
from ..utils.spec import parse_spec

_logger = getLogger(__name__)


class MapRaw:
    class _Spec(NamedTuple):
        flow_filter: TFilter
        path: Path

    _map_raw_request = "map_raw_request"
    _map_raw_response = "map_raw_response"

    def __init__(self):
        self._replacements_request: list[MapRaw._Spec] = []
        self._replacements_response: list[MapRaw._Spec] = []

    @staticmethod
    def _parse_option(option: str) -> MapRaw._Spec:
        _, flow_filter, path = parse_spec(option)

        try:
            path_resolved = Path(path).expanduser().resolve(True)

            if path_resolved.is_dir():
                raise ValueError(f"Invalid file path (directory): {path}.")

            return MapRaw._Spec(
                parse_flow_filter(flow_filter),
                path_resolved
            )
        except FileNotFoundError as fnfe:
            raise ValueError(f"Invalid file path (not found): {path} ({fnfe})") from fnfe

    @staticmethod
    def _parse_options(options: list[str]) -> Generator[MapRaw._Spec]:
        for option in options:
            try:
                yield MapRaw._parse_option(option)
            except ValueError as ve:
                raise OptionsError(f"Cannot parse map_raw option {option}: {ve}") from ve

    @staticmethod
    def _replace_response(flow: HTTPFlow, replacement: MapRaw._Spec) -> bool:
        try:
            with open(replacement.path, 'rb') as file:
                status_line = file.readline().strip().split(b' ', 2)

                protocol_version = status_code = status_text = None

                if len(status_line) == 2:
                    [protocol_version, status_code] = status_line
                elif len(status_line) == 3:
                    [protocol_version, status_code, status_text] = status_line
                else:
                    raise ValueError(f"Invalid HTTP message status line: {status_line}.")

                if isinstance(status_code, bytes):
                    status_code = int(status_code)

                headers = parse_headers(file)
                headers = {k: v for k, v in headers.items()}

                flow.response = Response.make(status_code, file.read(), headers)
                flow.response.http_version = protocol_version
                flow.response.reason = status_text

                return True
        except OSError as oe:
            _logger.warning(f'Failed read response replacement {replacement.path}.', exc_info=oe)
        except ValueError or TypeError as vte:
            _logger.warning(f'Failed to construct response replacement {replacement.path}.', exc_info=vte)

        return False

    @staticmethod
    def _replace_request(flow: HTTPFlow, replacement: MapRaw._Spec) -> bool:
        try:
            with open(replacement.path, 'rb') as file:
                request_line = file.readline().strip().split(b' ', 2)

                method = target = protocol_version = None

                if len(request_line) == 3:
                    [method, target, protocol_version] = request_line
                else:
                    raise ValueError(f"Invalid HTTP message request line: {request_line}.")

                if isinstance(method, bytes):
                    method = method.decode('ascii')

                if isinstance(target, bytes):
                    target = target.decode('ascii')

                headers = parse_headers(file)
                headers = {k: v for k, v in headers.items()}

                # TODO: implement request line processing, since `Request.make` expects a full url and target doesn't necessarily contain a full URL
                flow.request = Request.make(method, target, file.read(), headers)
                flow.request.http_version = protocol_version

                return True
        except OSError as oe:
            _logger.warning(f'Failed read request replacement {replacement.path}.', exc_info=oe)
        except ValueError or TypeError as vte:
            _logger.warning(f'Failed to construct request replacement {replacement.path}.', exc_info=vte)

        return False

    def configure(self, updated: set[str]):
        try:
            if self._map_raw_request in updated:
                self._replacements_request = list(self._parse_options(getattr(ctx.options, self._map_raw_request)))

            if self._map_raw_response in updated:
                self._replacements_response = list(self._parse_options(getattr(ctx.options, self._map_raw_response)))
        except:
            self._replacements_request = []
            self._replacements_response = []

            raise

    def load(self, loader: Loader):
        loader.add_option(
            self._map_raw_request,
            Sequence[str],
            [],
            """
            Map raw http request from local file using a pattern of the form
            "[/flow-filter]/file-path", where the separator can be any character.
            """
        )

        loader.add_option(
            self._map_raw_response,
            Sequence[str],
            [],
            """
            Map raw http response from local file using a pattern of the form
            "[/flow-filter]/file-path", where the separator can be any character.
            """
        )

    def request(self, flow: HTTPFlow):
        if flow.error or not flow.live:
            return

        if flow.response:
            for replacement in self._replacements_response:
                if replacement.flow_filter(flow) and self._replace_response(flow, replacement):
                    return

        for replacement in self._replacements_request:
            if replacement.flow_filter(flow) and self._replace_request(flow, replacement):
                return
