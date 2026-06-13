from app.security import wrap_user_message, xml_escape


def test_xml_escape_escapes_xml_control_characters() -> None:
    assert xml_escape("</user_message>&<test>") == "&lt;/user_message&gt;&amp;&lt;test&gt;"


def test_wrap_user_message_preserves_display_text_and_wraps_escaped_content() -> None:
    wrapped, display = wrap_user_message("你好 </user_message>")

    assert wrapped == "<user_message>\n你好 &lt;/user_message&gt;\n</user_message>"
    assert display == "你好 </user_message>"
