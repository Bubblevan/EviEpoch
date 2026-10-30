import unittest
from unittest.mock import Mock

import httpx
from openai import InternalServerError, RateLimitError

from eviepoch.providers.client import _chat_completion_with_one_extra_5xx_retry


def _api_error(error_type, status):
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return error_type("synthetic provider error", response=response, body=None)


class Provider5xxRetryTests(unittest.TestCase):
    def test_internal_server_error_gets_exactly_one_additional_single_shot_attempt(self):
        client = Mock()
        retry_client = Mock()
        expected = object()
        client.chat.completions.create.side_effect = _api_error(InternalServerError, 500)
        retry_client.chat.completions.create.return_value = expected
        client.with_options.return_value = retry_client

        result = _chat_completion_with_one_extra_5xx_retry(
            client, stage="attribute_support", model="synthetic-model", max_tokens=24
        )

        self.assertIs(result, expected)
        client.with_options.assert_called_once_with(max_retries=0)
        retry_client.chat.completions.create.assert_called_once_with(
            model="synthetic-model", max_tokens=24
        )

    def test_rate_limit_is_not_given_the_extra_5xx_retry(self):
        client = Mock()
        error = _api_error(RateLimitError, 429)
        client.chat.completions.create.side_effect = error

        with self.assertRaises(RateLimitError):
            _chat_completion_with_one_extra_5xx_retry(client, stage="attribute_support")

        client.with_options.assert_not_called()

    def test_failed_extra_5xx_attempt_is_not_retried_again(self):
        client = Mock()
        retry_client = Mock()
        client.chat.completions.create.side_effect = _api_error(InternalServerError, 500)
        retry_client.chat.completions.create.side_effect = _api_error(InternalServerError, 500)
        client.with_options.return_value = retry_client

        with self.assertRaises(InternalServerError):
            _chat_completion_with_one_extra_5xx_retry(client, stage="attribute_support")

        client.with_options.assert_called_once_with(max_retries=0)
        retry_client.chat.completions.create.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
