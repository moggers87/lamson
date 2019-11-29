from tempfile import mkdtemp
import os
import sys

from click import testing
from mock import Mock, patch

from salmon import queue, commands, encoding, mail, routing, utils

from .setup_env import SalmonTestCase


def make_fake_pid_file():
    with open("run/fake.pid", "w") as f:
        f.write("0")


class CliRunner(testing.CliRunner):
    def invoke(self, *args, **kwargs):
        kwargs.setdefault("catch_exceptions", False)
        return super(CliRunner, self).invoke(*args, **kwargs)


class CommandTestCase(SalmonTestCase):
    @patch("salmon.server.smtplib.SMTP")
    def test_send_command(self, client_mock):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("send", "--sender", "test@localhost", "--to", "test@localhost",
                                               "--body", "Test body", "--subject", "Test subject", "--attach",
                                               "setup.py", "--port", "8899", "--host", "127.0.0.1"))
        self.assertEqual(client_mock.return_value.sendmail.call_count, 1)
        self.assertEqual(result.exit_code, 0)

    def test_status_command(self):
        make_fake_pid_file()
        runner = CliRunner()
        running_result = runner.invoke(commands.main, ("status", "--pid", 'run/fake.pid'))
        self.assertEqual(running_result.output, "Salmon running with PID 0\n")
        self.assertEqual(running_result.exit_code, 0)

    def test_status_no_pid(self):
        runner = CliRunner()
        not_running_result = runner.invoke(commands.main, ("status", "--pid", 'run/donotexist.pid'))
        self.assertEqual(not_running_result.output, "Salmon not running.\n")
        self.assertEqual(not_running_result.exit_code, 1)

    def test_main(self):
        runner = CliRunner()
        result = runner.invoke(commands.main)
        self.assertEqual(result.exit_code, 0)

    @patch('salmon.queue.Queue')
    def test_queue_command(self, MockQueue):
        mq = MockQueue()
        mq.get.return_value = "A sample message"
        mq.keys.return_value = ["key1", "key2"]
        mq.pop.return_value = ('key1', 'message1')
        mq.count.return_value = 1

        runner = CliRunner()

        runner.invoke(commands.main, ("queue", "--pop"))
        self.assertEqual(mq.pop.call_count, 1)

        runner.invoke(commands.main, ("queue", "--get", "somekey"))
        self.assertEqual(mq.get.call_count, 1)

        runner.invoke(commands.main, ("queue", "--remove", "somekey"))
        self.assertEqual(mq.remove.call_count, 1)

        runner.invoke(commands.main, ("queue", "--clear"))
        self.assertEqual(mq.clear.call_count, 1)

        runner.invoke(commands.main, ("queue", "--keys"))
        self.assertEqual(mq.keys.call_count, 1)

        runner.invoke(commands.main, ("queue", "--count"))
        self.assertEqual(mq.count.call_count, 1)

    @patch('salmon.utils.daemonize', new=Mock())
    @patch('salmon.server.SMTPReceiver')
    def test_log_command(self, MockSMTPReceiver):
        runner = CliRunner()
        ms = MockSMTPReceiver()
        ms.start.function()

        result = runner.invoke(commands.main, ("log", "--host", "127.0.0.1", "--port", "8825", "--pid", "run/fake.pid"))
        self.assertEqual(utils.daemonize.call_count, 1)
        self.assertEqual(ms.start.call_count, 1)
        self.assertEqual(result.exit_code, 0)

        # test that it exits on existing pid
        make_fake_pid_file()
        result = runner.invoke(commands.main, ("log", "--host", "127.0.0.1", "--port", "8825", "--pid", "run/fake.pid"))
        self.assertEqual(result.exit_code, 1)

    @patch('sys.stdin', new=Mock())
    @patch("salmon.server.smtplib.SMTP")
    def test_sendmail_command(self, client_mock):
        sys.stdin.read.function()

        msg = mail.MailResponse(To="tests@localhost", From="tests@localhost",
                                Subject="Hello", Body="Test body.")
        sys.stdin.read.return_value = str(msg)

        runner = CliRunner()
        runner.invoke(commands.main, ("sendmail", "--host", "127.0.0.1", "--port", "8899", "test@localhost"))
        self.assertEqual(client_mock.return_value.sendmail.call_count, 1)

    @patch('salmon.utils.daemonize', new=Mock())
    @patch('salmon.utils.import_settings', new=Mock())
    @patch('salmon.utils.drop_priv', new=Mock())
    @patch('sys.path', new=Mock())
    def test_start_command(self):
        # normal start
        runner = CliRunner()
        runner.invoke(commands.main, ("start", "--pid", "smtp.pid"))
        self.assertEqual(utils.daemonize.call_count, 1)
        self.assertEqual(utils.import_settings.call_count, 1)

        # start with pid file existing already
        make_fake_pid_file()
        result = runner.invoke(commands.main, ("start", "--pid", "run/fake.pid"))
        self.assertEqual(result.exit_code, 1)

        # start with pid file existing and force given
        assert os.path.exists("run/fake.pid")
        runner.invoke(commands.main, ("start", "--force", "--pid", "run/fake.pid"))
        assert not os.path.exists("run/fake.pid")

        # start with a uid but no gid
        runner.invoke(commands.main, ("start", "--uid", "1000", "--pid", "run/fake.pid", "--force"))
        self.assertEqual(utils.drop_priv.call_count, 0)

        # start with a uid/gid given that's valid
        runner.invoke(commands.main, ("start", "--uid", "1000", "--gid", "1000", "--pid", "run/fake.pid", "--force"))
        self.assertEqual(utils.drop_priv.call_count, 1)

        # non daemon start
        daemonize_call_count = utils.daemonize.call_count
        runner.invoke(commands.main, ("start", "--pid", "run/fake.pid", "--no-daemon", "--force"))
        self.assertEqual(utils.daemonize.call_count, daemonize_call_count)  # same count -> not called

    def test_cleanse_command(self):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("cleanse", "run/queue", "run/cleansed"))
        self.assertEqual(result.exit_code, 0)
        assert os.path.exists('run/cleansed')

    @patch('salmon.encoding.from_message')
    def test_cleanse_command_with_encoding_error(self, from_message):
        runner = CliRunner()
        from_message.side_effect = encoding.EncodingError
        in_queue = "run/queue"
        q = queue.Queue(in_queue)
        q.push("hello")

        result = runner.invoke(commands.main, ("cleanse", in_queue, "run/cleased"))
        self.assertEqual(result.exit_code, 1)

    def test_blast_command(self):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("blast", "--host", "127.0.0.1", "--port", "8899", "run/queue"))
        self.assertEqual(result.exit_code, 0)


class GenCommandTestCase(SalmonTestCase):
    def setUp(self):
        super(GenCommandTestCase, self).setUp()
        tmp_dir = mkdtemp()
        self.project = os.path.join(tmp_dir, 'testproject')

    def test_gen_command(self):
        runner = CliRunner()

        result = runner.invoke(commands.main, ("gen", self.project))
        assert os.path.exists(self.project)
        self.assertEqual(result.exit_code, 0)

    def test_if_folder_exists(self):
        runner = CliRunner()
        os.mkdir(self.project)

        result = runner.invoke(commands.main, ("gen", self.project))
        self.assertEqual(result.exit_code, 1)

    def test_force(self):
        runner = CliRunner()

        # folder doesn't exist, but user has used --force anyway
        result = runner.invoke(commands.main, ("gen", self.project, "--force"))
        assert os.path.exists(self.project)
        self.assertEqual(result.exit_code, 0)

        # assert again, this time the folder exists
        result = runner.invoke(commands.main, ("gen", self.project, "--force"))
        assert os.path.exists(self.project)
        self.assertEqual(result.exit_code, 0)


class StopCommandTestCase(SalmonTestCase):
    def setUp(self):
        super(StopCommandTestCase, self).setUp()
        patcher = patch("os.kill")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stop_command(self):
        runner = CliRunner()
        make_fake_pid_file()
        result = runner.invoke(commands.main, ("stop", "--pid", "run/fake.pid"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(os.kill.call_count, 1)

    def test_stop_pid_doesnt_exist(self):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("stop", "--pid", "run/dontexit.pid"))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(os.kill.call_count, 0)

    @patch('glob.glob', new=lambda x: ['run/fake.pid'])
    def test_stop_all(self):
        runner = CliRunner()
        make_fake_pid_file()
        result = runner.invoke(commands.main, ("stop", "--all", "run"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(os.kill.call_count, 1)

    def test_stop_force(self):
        runner = CliRunner()
        make_fake_pid_file()
        result = runner.invoke(commands.main, ("stop", "--pid", "run/fake.pid", "--force"))
        self.assertEqual(os.kill.call_count, 1)
        self.assertEqual(result.exit_code, 0)
        assert not os.path.exists("run/fake.pid")

    def test_stop_force_oserror(self):
        runner = CliRunner()
        make_fake_pid_file()
        os.kill.side_effect = OSError("Fail")
        result = runner.invoke(commands.main, ("stop", "--pid", "run/fake.pid", "--force"))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(os.kill.call_count, 1)


class RoutesCommandTestCase(SalmonTestCase):
    def setUp(self):
        super(RoutesCommandTestCase, self).setUp()
        if "salmon.handlers.log" in sys.modules:
            del sys.modules["salmon.handlers.log"]
        routing.Router.clear_routes()
        routing.Router.clear_states()
        routing.Router.HANDLERS.clear()

    def test_no_args(self):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("routes",))
        self.assertEqual(result.exit_code, 2)

    def test_not_importable(self):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("routes", "not_a_module", "--test", "user@example.com"))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.output,
                         ("Error: Module 'not_a_module' could not be imported. "
                          "Did you forget to use the --path option?\n"))

    def test_match(self):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("routes", "salmon.handlers.log", "--test", "user@example.com"))
        self.assertEqual(result.exit_code, 0)
        # TODO: use groupdict directly once Python 2.7 support has been dropped
        match_items = [i for i in
                       routing.Router.REGISTERED.values()][0][0].match("user@example.com").groupdict().items()
        self.assertEqual(result.output,
                         ("Routing ORDER: ['^(?P<to>.+)@(?P<host>.+)$']\n"
                          "Routing TABLE:\n"
                          "---\n"
                          "'^(?P<to>.+)@(?P<host>.+)$': salmon.handlers.log.START \n"
                          "---\n"
                          "\n"
                          "TEST address 'user@example.com' matches:\n"
                          "  '^(?P<to>.+)@(?P<host>.+)$' salmon.handlers.log.START\n"
                          "  -  %r\n" % {str(k): str(v) for k, v in match_items}))

    def test_no_match(self):
        runner = CliRunner()
        result = runner.invoke(commands.main, ("routes", "salmon.handlers.log", "--test", "userexample.com"))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.output,
                         ("Routing ORDER: ['^(?P<to>.+)@(?P<host>.+)$']\n"
                          "Routing TABLE:\n"
                          "---\n"
                          "'^(?P<to>.+)@(?P<host>.+)$': salmon.handlers.log.START \n"
                          "---\n"
                          "\n"
                          "TEST address 'userexample.com' didn't match anything.\n"))