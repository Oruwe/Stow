# Dockerfile Optimizer Explainability

## Decision Reasoning
The agent evaluates Dockerfile instruction ordering and dependency layer caches to determine opportunities for image size minimization and build acceleration. It decides which base image recommendations to surface by cross-referencing multi-stage build patterns against security vulnerability benchmarks.

## Data Inputs
The primary data input is the raw content of the target Dockerfile and associated build context manifests. Additionally, it ingests developer configuration flags specifying deployment architectures and runtime constraints.

## Known Limitations
One major constraint is that the agent cannot execute dynamic container run tests to detect runtime environment variable failures. Another known issue is that proprietary base image layers cannot be introspected without external container registry credentials.
