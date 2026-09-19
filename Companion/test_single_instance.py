import unittest

import single_instance


class SingleInstanceExitTests(unittest.TestCase):
    def test_explicit_release_arms_exit_watchdog(self):
        calls = []
        original_release_only = single_instance._release_only
        original_watchdog = single_instance._start_exit_watchdog
        original_live_threads = single_instance._live_threads
        try:
            single_instance._release_only = lambda: calls.append("release")
            single_instance._start_exit_watchdog = lambda: calls.append("watchdog")
            single_instance._live_threads = lambda: "test"
            single_instance.release()
        finally:
            single_instance._release_only = original_release_only
            single_instance._start_exit_watchdog = original_watchdog
            single_instance._live_threads = original_live_threads

        self.assertEqual(calls, ["release", "watchdog"])

    def test_watchdog_is_not_part_of_atexit_handle_release(self):
        # Interpreter shutdown uses _release_only directly; the hard-stop path
        # is reserved for an explicit Exit request from the application.
        self.assertIsNot(single_instance.release, single_instance._release_only)


if __name__ == "__main__":
    unittest.main()
