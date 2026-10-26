from pathlib import Path

from setuptools import setup, find_packages

import microservice_utils

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

# The pinecone adapter imports SentenceTransformer at module scope, so the
# pinecone extra needs this too. Keep the two in step.
sentence_transformers_require = ["sentence-transformers>=5,<6"]

setup(
    name="microservice-utils",
    license="MIT",
    long_description=long_description,
    long_description_content_type="text/markdown",
    version=microservice_utils.__version__,
    extras_require={
        "events": ["pydantic>=2,<3"],
        "gcp_cloud_run": ["google-cloud-run<1"],
        "gcp_cloud_tasks": ["google-cloud-tasks>=2,<3"],
        "gcp_pubsub": ["google-cloud-pubsub>=2,<3", "tenacity>=8,<9"],
        "gcp_storage": ["gcloud-aio-storage>=8,<9"],
        "novu": ["novu-py==3.13.0"],
        "openai": ["masked-ai>=1,<2", "openai>=1,<2"],
        "pinecone": ["pinecone>=7,<8", *sentence_transformers_require],
        "nltk": ["nltk==3.9.1"],
        "telnyx": ["requests>=2,<3"],
        "mailchimp_transactional": ["mailchimp-transactional==1.0.56"],
        "sentence_transformers": sentence_transformers_require,
    },
    install_requires=[
        "ulid-py>=1,<2",
        "httpx==0.28.1",
    ],
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.11",
)
