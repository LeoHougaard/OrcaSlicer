#!/usr/bin/env python3
"""Local web server for the continuous path lab."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LAB_DIR = Path(__file__).resolve().parent
STATIC_DIR = LAB_DIR / "static"
if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))

import geometry_io  # noqa: E402
import planner  # noqa: E402


def json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


class LabHandler(BaseHTTPRequestHandler):
    server_version = "ContinuousPathLab/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_payload(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/shapes":
                shapes = []
                for name, model in sorted(planner.available_shapes().items()):
                    shapes.append(
                        {
                            "name": name,
                            "bounds": list(model.bounds(0.0)),
                            "holes": len(model.holes),
                        }
                    )
                self.send_payload(*json_bytes({"shapes": shapes}))
                return

            path = self.path.split("?", 1)[0]
            if path == "/":
                path = "/index.html"
            target = (STATIC_DIR / path.lstrip("/")).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.send_payload(200, target.read_bytes(), ctype)
        except Exception:
            traceback.print_exc()
            self.send_payload(*json_bytes({"error": "Internal server error"}, status=500))

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/inspect":
                payload = self.read_json()
                name, data = geometry_io.decode_file_payload(payload)
                suffix = Path(name).suffix.lower()
                if suffix == ".stl":
                    info = geometry_io.inspect_stl(data)
                    info["fileName"] = name
                    self.send_payload(*json_bytes(info))
                    return
                if suffix == ".dxf":
                    model, diagnostics = geometry_io.model_from_dxf(data, name)
                    self.send_payload(
                        *json_bytes(
                            {
                                "type": "dxf",
                                "fileName": name,
                                "bounds": list(model.bounds(0.0)),
                                "outerPoints": len(model.outer),
                                "holes": len(model.holes),
                                "diagnostics": diagnostics,
                            }
                        )
                    )
                    return
                raise geometry_io.InputError("Supported file types are DXF and STL.")

            if self.path == "/api/generate":
                payload = self.read_json()
                model, input_diagnostics, meta = geometry_io.load_model_from_request(payload)
                result = planner.plan_model(model, payload.get("options") or {})
                result["input"] = meta
                result["diagnostics"] = input_diagnostics + list(result.get("diagnostics", []))
                self.send_payload(*json_bytes(result))
                return

            if self.path == "/api/preview":
                payload = self.read_json()
                model, input_diagnostics, meta = geometry_io.load_model_from_request(payload)
                self.send_payload(
                    *json_bytes(
                        {
                            "model": planner.model_payload(model),
                            "input": meta,
                            "diagnostics": input_diagnostics,
                        }
                    )
                )
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        except geometry_io.InputError as exc:
            self.send_payload(*json_bytes({"error": str(exc)}, status=400))
        except Exception as exc:
            traceback.print_exc()
            self.send_payload(*json_bytes({"error": f"Internal server error: {exc}"}, status=500))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), LabHandler)
    print(f"Continuous path lab running at http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping continuous path lab.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
