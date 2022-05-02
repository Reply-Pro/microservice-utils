from setuptools import setup, find_packages

import microservice_utils


setup(
    name="microservice-utils",
    version=microservice_utils.__version__,
    extras_require={
        "events": ["pydantic>1,<2"],
    },
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.9",
)
