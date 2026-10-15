import io
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.conf.mineru_config import mineru_config
from app.import_process.agent.nodes.node_pdf_to_md import (
    step_2_parse_with_local_mineru_api,
    step_2_run_local_mineru,
)


class LocalMineruCliTest(unittest.TestCase):
    def test_runs_cli_and_finds_markdown(self):
        original_cli_path = mineru_config.cli_path
        original_output_root = mineru_config.output_root

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                pdf_path = root / "manual.pdf"
                pdf_path.write_bytes(b"%PDF-test")
                cli_path = root / "magic-pdf.exe"
                cli_path.write_bytes(b"")
                expected_md = root / "output" / "mineru" / "manual" / "manual" / "auto" / "manual.md"
                expected_md.parent.mkdir(parents=True)
                expected_md.write_text("# parsed", encoding="utf-8")

                mineru_config.cli_path = str(cli_path)
                mineru_config.output_root = None
                completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="done")
                with patch(
                    "app.import_process.agent.nodes.node_pdf_to_md.subprocess.run",
                    return_value=completed,
                ) as run_mock:
                    md_path = step_2_run_local_mineru(pdf_path, root / "output")

                self.assertEqual(Path(md_path), expected_md.resolve())
                command = run_mock.call_args.args[0]
                self.assertEqual(command[0], str(cli_path))
                self.assertIn("--path", command)
                self.assertIn("--output-dir", command)
        finally:
            mineru_config.cli_path = original_cli_path
            mineru_config.output_root = original_output_root


class LocalMineruApiTest(unittest.TestCase):
    def test_reuses_completed_upload_for_duplicate_file(self):
        original_base_url = mineru_config.local_base_url
        original_tier = mineru_config.local_tier
        original_poll_interval = mineru_config.local_poll_interval
        original_timeout_seconds = mineru_config.local_timeout_seconds

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                pdf_path = root / "manual.pdf"
                pdf_path.write_bytes(b"%PDF-test")
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as archive:
                    archive.writestr("manual/full.md", "# parsed")

                duplicate_upload_response = Mock(status_code=200)
                duplicate_upload_response.json.return_value = {
                    "id": "upload-existing",
                    "status": "completed",
                    "file": {"id": "source-file-existing"},
                }
                create_job_response = Mock(status_code=202)
                create_job_response.json.return_value = {"job_id": "job-1"}
                job_response = Mock(status_code=200)
                job_response.json.return_value = {
                    "status": "completed",
                    "files": [{
                        "status": "completed",
                        "output_files": {"zip": {"file_id": "output-zip"}},
                    }],
                }
                zip_response = Mock(status_code=200, content=zip_buffer.getvalue())

                mineru_config.local_base_url = "http://127.0.0.1:8100"
                mineru_config.local_tier = "standard"
                mineru_config.local_poll_interval = 0
                mineru_config.local_timeout_seconds = 5
                with patch(
                    "app.import_process.agent.nodes.node_pdf_to_md.requests.post",
                    side_effect=[duplicate_upload_response, create_job_response],
                ) as post_mock, patch(
                    "app.import_process.agent.nodes.node_pdf_to_md.requests.put",
                ) as put_mock, patch(
                    "app.import_process.agent.nodes.node_pdf_to_md.requests.get",
                    side_effect=[job_response, zip_response],
                ):
                    md_path = step_2_parse_with_local_mineru_api(pdf_path, root / "output")

                self.assertEqual(Path(md_path).read_text(encoding="utf-8"), "# parsed")
                self.assertFalse(put_mock.called)
                self.assertEqual(
                    post_mock.call_args_list[1].kwargs["json"]["files"][0]["source"]["file_id"],
                    "source-file-existing",
                )
        finally:
            mineru_config.local_base_url = original_base_url
            mineru_config.local_tier = original_tier
            mineru_config.local_poll_interval = original_poll_interval
            mineru_config.local_timeout_seconds = original_timeout_seconds

    def test_parses_local_file_and_extracts_zip_result(self):
        original_base_url = mineru_config.local_base_url
        original_tier = mineru_config.local_tier
        original_poll_interval = mineru_config.local_poll_interval
        original_timeout_seconds = mineru_config.local_timeout_seconds

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                pdf_path = root / "manual.pdf"
                pdf_path.write_bytes(b"%PDF-test")
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as archive:
                    archive.writestr("manual/full.md", "# parsed")
                    archive.writestr("manual/images/page_1.jpg", b"image")

                create_upload_response = Mock(status_code=200)
                create_upload_response.json.return_value = {"id": "upload-1"}
                complete_upload_response = Mock(status_code=200)
                complete_upload_response.json.return_value = {"file": {"id": "source-file"}}
                create_job_response = Mock(status_code=202)
                create_job_response.json.return_value = {"job_id": "job-1"}
                job_response = Mock(status_code=200)
                job_response.json.return_value = {
                    "status": "completed",
                    "files": [{
                        "status": "completed",
                        "output_files": {"zip": {"file_id": "output-zip"}},
                    }],
                }
                zip_response = Mock(status_code=200, content=zip_buffer.getvalue())

                mineru_config.local_base_url = "http://127.0.0.1:8100"
                mineru_config.local_tier = "standard"
                mineru_config.local_poll_interval = 0
                mineru_config.local_timeout_seconds = 5
                with patch(
                    "app.import_process.agent.nodes.node_pdf_to_md.requests.post",
                    side_effect=[
                        create_upload_response,
                        complete_upload_response,
                        create_job_response,
                    ],
                ) as post_mock, patch(
                    "app.import_process.agent.nodes.node_pdf_to_md.requests.put",
                    return_value=Mock(status_code=200),
                ) as put_mock, patch(
                    "app.import_process.agent.nodes.node_pdf_to_md.requests.get",
                    side_effect=[job_response, zip_response],
                ):
                    md_path = step_2_parse_with_local_mineru_api(pdf_path, root / "output")

                self.assertEqual(Path(md_path).read_text(encoding="utf-8"), "# parsed")
                self.assertTrue((Path(md_path).parent / "images" / "page_1.jpg").exists())
                self.assertEqual(post_mock.call_args.args[0], "http://127.0.0.1:8100/v1/parse/jobs")
                self.assertEqual(post_mock.call_args.kwargs["json"]["files"][0]["source"]["type"], "file_id")
                self.assertEqual(put_mock.call_args.args[0], "http://127.0.0.1:8100/v1/uploads/upload-1/content")
        finally:
            mineru_config.local_base_url = original_base_url
            mineru_config.local_tier = original_tier
            mineru_config.local_poll_interval = original_poll_interval
            mineru_config.local_timeout_seconds = original_timeout_seconds


if __name__ == "__main__":
    unittest.main()
