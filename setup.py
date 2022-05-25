from setuptools import setup, find_packages

import microservice_utils


setup(
    name="microservice-utils",
    version=microservice_utils.__version__,
    extras_require={
        "events": ["pydantic>=1,<2"],
        "gcp_cloud_run": ["google-cloud-run<1"],
        "gcp_cloud_tasks": ["google-cloud-tasks>=2,<3"],
        "gcp_pubsub": ["google-cloud-pubsub>=2,<3", "tenacity>=8,<9"],
    },
    install_requires=[
        "ulid-py>=1,<2",
    ],
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.9",
)
