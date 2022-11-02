# microservice-utils

Utilities and adapters for speeding up microservice development.

## Releasing a new version
- Update the package version using semver rules (`microservice-utils/__init__.py`)
- Commit and push change
- Create a new tag with the version (e.g. `git tag -a vx.x.x -m ''`)
- `git push --tags` to push the new tag and start the release workflow

## Todos

- [x] Events
- [x] GCP Pub/Sub
- [x] GCP Cloud Tasks
- [ ] JWT validation utils
- [x] Logging
