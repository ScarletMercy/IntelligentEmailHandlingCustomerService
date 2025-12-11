import imaplib
import email
import time
import os
from email.header import decode_header
from queue import Queue
import logging


class QQEmailListener:
    def __init__(self, email_address, password):
        self.email_address = email_address
        self.password = password
        self.imap_server = "imap.qq.com"
        self.imap_port = 993
        self.email_queue = Queue()
        self.logger = logging.getLogger(__name__)

    def _connect_and_select(self):
        """连接并选择收件箱"""
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_address, self.password)
            mail.select('inbox')
            self.logger.info("成功连接到QQ邮箱")
            return mail
        except Exception as e:
            self.logger.error(f"连接失败: {e}")
            return None

    def _disconnect(self, mail):
        """安全断开连接"""
        try:
            if mail:
                mail.close()
                mail.logout()
        except:
            pass

    def _get_unread_emails(self, mail):
        """获取并处理未读邮件"""
        try:
            # 搜索未读邮件
            status, message_ids = mail.search(None, 'UNSEEN')
            if status != 'OK':
                return []

            email_ids = message_ids[0].split()
            if not email_ids:
                return []

            unread_emails = []
            for email_id in email_ids:
                # 获取邮件
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                if status != 'OK':
                    continue

                # 解析邮件
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)

                # 提取邮件信息
                subject = self._decode_subject(email_message['Subject'])
                sender = email_message['From']
                date = email_message['Date']

                # 解析邮件内容
                content = self._get_email_content(email_message)

                email_info = {
                    'id': email_id.decode(),
                    'subject': subject,
                    'from': sender,
                    'date': date,
                    'content': content
                }

                unread_emails.append(email_info)

                # 立即标记为已读
                mail.store(email_id, '+FLAGS', '\\Seen')

            return unread_emails

        except Exception as e:
            self.logger.error(f"获取未读邮件时出错: {e}")
            return []

    def _decode_subject(self, subject):
        """解码邮件主题"""
        if subject:
            decoded_fragments = decode_header(subject)
            decoded_subject = ""
            for fragment, encoding in decoded_fragments:
                if isinstance(fragment, bytes):
                    if encoding:
                        try:
                            decoded_subject += fragment.decode(encoding)
                        except:
                            try:
                                decoded_subject += fragment.decode('utf-8')
                            except:
                                decoded_subject += fragment.decode('latin1', errors='ignore')
                    else:
                        try:
                            decoded_subject += fragment.decode('utf-8', errors='ignore')
                        except:
                            decoded_subject += fragment.decode('latin1', errors='ignore')
                else:
                    decoded_subject += fragment
            return decoded_subject
        return ""

    def _get_email_content(self, email_message):
        """提取邮件内容"""
        content = ""
        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" or content_type == "text/html":
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset()
                            if charset:
                                try:
                                    content = payload.decode(charset)
                                except:
                                    try:
                                        content = payload.decode('utf-8')
                                    except:
                                        content = payload.decode('latin1', errors='ignore')
                            else:
                                try:
                                    content = payload.decode('utf-8', errors='ignore')
                                except:
                                    content = payload.decode('latin1', errors='ignore')
                    except Exception as e:
                        self.logger.error(f"解码邮件内容时出错: {e}")
                        pass
        else:
            try:
                payload = email_message.get_payload(decode=True)
                if payload:
                    charset = email_message.get_content_charset()
                    if charset:
                        try:
                            content = payload.decode(charset)
                        except:
                            try:
                                content = payload.decode('utf-8')
                            except:
                                content = payload.decode('latin1', errors='ignore')
                    else:
                        try:
                            content = payload.decode('utf-8', errors='ignore')
                        except:
                            content = payload.decode('latin1', errors='ignore')
            except Exception as e:
                self.logger.error(f"解码邮件内容时出错: {e}")
                pass
        return content

    def listen_for_emails(self, check_interval=30):
        """监听新邮件（每次轮询只建立一次连接）"""
        self.logger.info("开始监听新邮件...")

        # 初始化：首次运行时标记所有历史未读邮件为已读
        initial_mail = self._connect_and_select()
        if initial_mail:
            try:
                status, message_ids = initial_mail.search(None, 'UNSEEN')
                if status == 'OK':
                    old_count = len(message_ids[0].split())
                    if old_count > 0:
                        # 标记所有历史未读邮件为已读
                        for email_id in message_ids[0].split():
                            initial_mail.store(email_id, '+FLAGS', '\\Seen')
                        self.logger.info(f"初始化完成，已将 {old_count} 封历史未读邮件标记为已读")
                    else:
                        self.logger.info("初始化完成，当前无历史未读邮件")
            except Exception as e:
                self.logger.error(f"初始化标记历史邮件时出错: {e}")
            finally:
                self._disconnect(initial_mail)

        retry_delay = check_interval

        while True:
            try:
                # 每次只建立一次连接，完成所有操作
                mail = self._connect_and_select()
                if not mail:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 300)
                    continue

                try:
                    # 获取新邮件
                    new_emails = self._get_unread_emails(mail)

                    if new_emails:
                        self.logger.info(f"发现 {len(new_emails)} 封新邮件!")
                        for email_info in new_emails:
                            new_email = {
                                "email_id": email_info['id'],
                                "sender_email": email_info['from'],
                                "email_content": {
                                    "主题": email_info['subject'],
                                    "内容预览": email_info['content'][:100],
                                    "日期": email_info['date']
                                }
                            }
                            self.email_queue.put(new_email)

                    # 处理队列中的邮件
                    while not self.email_queue.empty():
                        yield self.email_queue.get()

                    retry_delay = check_interval  # 成功后重置重试延迟

                finally:
                    self._disconnect(mail)

                # 等待下一次轮询
                time.sleep(check_interval)

            except KeyboardInterrupt:
                self.logger.info("停止监听")
                break
            except Exception as e:
                self.logger.error(f"监听过程中出错: {e}")
                time.sleep(min(retry_delay, 300))
                retry_delay *= 2



