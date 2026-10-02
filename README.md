# Telecom Support Resolution Assistant

A local-first, microservice-based assistant for telecom support agents. It classifies a customer complaint, retrieves relevant resolved tickets and knowledge-base articles, and drafts a cited resolution for agent review.

The project is being built in documented phases. The current repository includes a synthetic telecom dataset; service implementation follows. See [the dataset guide](data/synthetic/v1/README.md), [phase records](docs/phases/), and [issues and errors](docs/issues-and-errors.md).

The data and generated outcomes are synthetic. The assistant must not present an unresolved ticket as a successful past resolution.
