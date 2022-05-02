from setuptools import setup, find_packages

import microservice_utils


setup(
    name="microservice-utils",
    version=microservice_utils.__version__,
    install_requires=[
    ],
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.8",
)
