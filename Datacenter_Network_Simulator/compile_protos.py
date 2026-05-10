"""
Developer helper: compile proto/gnmi.proto → proto/compiled/gnmi_pb2.py + gnmi_pb2_grpc.py

Usage:
    python compile_protos.py

Requires grpcio-tools:
    pip install grpcio-tools
"""
import sys
from pathlib import Path


def main():
    proto_dir   = Path(__file__).parent / "proto"
    compiled_dir = proto_dir / "compiled"
    compiled_dir.mkdir(parents=True, exist_ok=True)
    (compiled_dir / "__init__.py").write_text(
        "# Auto-generated gNMI proto stubs.\n"
    )

    try:
        from grpc_tools import protoc
    except ImportError:
        print("ERROR: grpcio-tools not installed.  Run:  pip install grpcio-tools")
        sys.exit(1)

    proto_file = proto_dir / "gnmi.proto"
    if not proto_file.exists():
        print(f"ERROR: {proto_file} not found.")
        sys.exit(1)

    args = [
        "grpc_tools.protoc",
        f"--proto_path={proto_dir}",
        f"--python_out={compiled_dir}",
        f"--grpc_python_out={compiled_dir}",
        str(proto_file),
    ]
    print("Compiling:", " ".join(args[1:]))
    ret = protoc.main(args)
    if ret == 0:
        print("Done — stubs written to", compiled_dir)
    else:
        print("protoc exited with code", ret)
        sys.exit(ret)


if __name__ == "__main__":
    main()