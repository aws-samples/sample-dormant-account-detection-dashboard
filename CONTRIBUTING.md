# Contributing Guidelines

Thank you for your interest in contributing to this project. Whether it's a bug report, new feature, correction, or additional documentation, we value feedback and contributions from the community.

Read through this document before you submit any issues or pull requests, so that we have the information we need to respond to your contribution.

## Reporting bugs and feature requests

Use the issue tracker to report bugs or suggest features. Before you open an issue, check existing open and recently closed issues to see whether someone else already reported it. Include as much of the following as you can:

- A reproducible test case or series of steps
- The version of the templates being used
- Any modifications you've made that are relevant to the bug
- Anything unusual about your environment or deployment (for example, AWS Region, AWS Control Tower configuration, or AWS Config aggregator setup)

## Contributing through pull requests

Contributions through pull requests are welcome. Before you send a pull request, make sure that:

1. You are working against the latest source on the `main` branch.
2. You checked existing open and recently merged pull requests to confirm that nobody else has addressed the problem.
3. You opened an issue to discuss any significant work.

To send a pull request:

1. Fork the repository.
2. Change the source. Focus on the specific change you are contributing. If you also reformat all the code, it's hard for us to focus on your change.
3. Run the checks that the `validate` GitHub Actions workflow runs on every pull request, then validate with `checkov` and deploy the change to a test environment:

   ```bash
   pip install "cfn-lint==1.51.4"
   cfn-lint --non-zero-exit-code error cloudformation/*.yaml
   python3 scripts/check_inline_lambda_code.py cloudformation/*.yaml
   ```
4. Commit to your fork using clear commit messages.
5. Send a pull request and answer any default questions in the pull request interface.
6. Pay attention to any automated CI failures reported in the pull request, and stay involved in the conversation.

## Finding contributions to work on

Issues labeled `help wanted` are a good place to start.

## Code of Conduct

This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct). For more information, see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact opensource-codeofconduct@amazon.com with any additional questions or comments.

## Security issue notifications

If you discover a potential security issue in this project, notify AWS Security through the [vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/). Do **not** create a public issue. See [SECURITY.md](SECURITY.md).

## Licensing

See the [LICENSE](LICENSE) file for this project's licensing. We will ask you to confirm the licensing of your contribution.
