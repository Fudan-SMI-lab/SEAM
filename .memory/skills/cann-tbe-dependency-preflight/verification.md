# Verification for CANN TBE Dependency Pre-flight: Install decorator, attrs, psutil, cffi, protobuf Before CANN Operator Execution

- Source run: e2e-v3-8c8bf406dc7e
- Verify by running the pre-flight checker: .venv/bin/python -c "[__import__(m) for m in ['decorator','attrs','scipy','psutil','cffi','protobuf']]"
