from setuptools import setup, find_packages

import microservice_utils


setup(
    name="microservice-utils",
    version=microservice_utils.__version__,
    extras_require={
        "events": ["pydantic>1,<2"],
        "cloud_tasks": ["google-cloud-tasks>=2.0.0,<3.0.0", "ulid-py==1.1.0"],
    },
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.9",
)
