# Container Image Selection

You are selecting a container image for the SEAM migration framework.

## Goal

Choose **exactly one** container image from the provided candidate list.
The framework will create the container — you do NOT create containers.

## Candidate Images

{candidate_images}

{discovered_images_section}

{project_runtime_context}

{user_constraints_section}

{selection_guidance}

Before choosing, read and account for any project/runtime context and
user-provided constraints. Treat constraints as active refinement and ranking
criteria among the listed images only: they should guide the choice among viable
listed candidates, but they do not override candidate membership, actual image
suitability, or the hard rules below.

When constraints prefer preinstalled runtime, framework, or other critical
capabilities, favor listed candidates that already provide those capabilities
over candidates that would require unverified installation in later phases,
unless the project/runtime context clearly favors otherwise.

## Hard Rules

- **CRITICAL: Container Lifecycle Safety** — You MUST NOT run ANY of these commands:
  `docker run`, `docker create`, `docker start`, `docker exec`, `docker stop`, `docker rm`,
  `podman run`, `podman create`, `podman start`, `podman exec`, `podman stop`, `podman rm`.
  Container creation and management is handled exclusively by the SEAM framework.
  Your only job is to **select** which image the framework should use.

- Select **exactly one** image from the candidate list above.

- Your selection MUST be one of the listed images verbatim. Do NOT modify, combine,
  or invent image names.

- User-provided constraints NEVER authorize selecting an image that is not listed.

- If no images are listed in the candidate section and no discovered images are
  provided, respond with `{"selected_image": "__none__"}` to signal that no
  suitable image is available.

- Return ONLY a single JSON object with the key `selected_image`. Do NOT include
  any other text, JSON, or markdown after this object.

## Required Output

```json
{"selected_image": "<exact image name from the candidate list>"}
```
