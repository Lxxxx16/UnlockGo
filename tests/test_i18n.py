from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app import i18n


class I18nTests(unittest.TestCase):
    def setUp(self) -> None:
        i18n.set_language(i18n.DEFAULT_LANGUAGE)

    def tearDown(self) -> None:
        i18n.set_language(i18n.DEFAULT_LANGUAGE)

    def test_default_language_is_chinese(self) -> None:
        self.assertEqual(i18n.DEFAULT_LANGUAGE, "zh_CN")
        self.assertEqual(i18n.get_language(), "zh_CN")
        self.assertEqual(i18n.tr('文件'), '文件')

    def test_translates_known_text_and_formats_placeholders(self) -> None:
        i18n.set_language("en_US")

        self.assertEqual(i18n.tr('文件'), "File")
        self.assertEqual(
            i18n.tr('目标列表 ({count})', count=3),
            "Targets (3)",
        )

    def test_unknown_text_falls_back_to_chinese_source(self) -> None:
        i18n.set_language("en_US")
        source = "\u6b64\u6587\u6848\u5c1a\u672a\u7ffb\u8bd1"

        self.assertEqual(i18n.tr(source), source)

    def test_invalid_language_falls_back_to_chinese(self) -> None:
        self.assertEqual(i18n.set_language("fr_FR"), "zh_CN")
        self.assertEqual(i18n.get_language(), "zh_CN")

    def test_english_operation_messages_are_grammatical(self) -> None:
        i18n.set_language("en_US")

        self.assertEqual(
            i18n.tr('{operation}目标必须是一个已存在的文件夹。', operation="Copy"),
            "The destination folder for this operation must exist.",
        )
        self.assertEqual(
            i18n.tr('{operation}目标文件夹无法访问：{path}', operation="Move", path="C:/tmp"),
            "The destination folder for this operation is inaccessible: C:/tmp",
        )
        self.assertEqual(
            i18n.tr('不能把文件夹{operation}到它自身或子文件夹中。', operation="Copy"),
            "A folder cannot be moved or copied into itself or a subfolder.",
        )
        self.assertEqual(
            i18n.tr('目标位置与当前位置相同，无需{operation}。', operation="Move"),
            "The destination is the same as the current location; no operation is needed.",
        )

    def test_load_language_uses_saved_setting(self) -> None:
        settings = Mock()
        settings.value.return_value = "en_US"

        with patch.object(i18n, "QSettings", return_value=settings):
            self.assertEqual(i18n.load_language(), "en_US")

        settings.value.assert_called_once_with(
            i18n.LANGUAGE_SETTING_KEY,
            i18n.DEFAULT_LANGUAGE,
        )
        self.assertEqual(i18n.get_language(), "en_US")

    def test_save_language_normalizes_and_syncs_setting(self) -> None:
        settings = Mock()

        with patch.object(i18n, "QSettings", return_value=settings):
            i18n.save_language("fr_FR")

        settings.setValue.assert_called_once_with(i18n.LANGUAGE_SETTING_KEY, "zh_CN")
        settings.sync.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
