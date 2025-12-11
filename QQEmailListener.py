import imaplib
import email
import asyncio
import time
import os
from email.header import decode_header
import threading
from queue import Queue


class QQEmailListener:
    def __init__(self, email_address, password):
        self.email_address = email_address
        self.password = password
        self.imap_server = "imap.qq.com"
        self.imap_port = 993
        self.mail = None
        self.should_stop = False
        self.mail_lock = threading.Lock()
        self.email_queue = Queue()  # 用于存储检测到的邮件

    def connect(self):
        """连接到QQ邮箱IMAP服务器"""
        try:
            # 创建IMAP4 SSL连接
            self.mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            # 登录
            self.mail.login(self.email_address, self.password)
            # 选择收件箱
            self.mail.select('inbox')
            print("成功连接到QQ邮箱")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        try:
            if self.mail:
                self.mail.close()
                self.mail.logout()
                self.mail = None
                print("已断开邮箱连接")
        except:
            pass

    def check_new_emails(self):
        """检查新邮件"""
        try:
            # 获取当前邮件总数
            status, messages = self.mail.select('inbox')
            if status != 'OK':
                return []

            # 获取最新的邮件ID
            status, message_ids = self.mail.search(None, 'ALL')
            if status != 'OK':
                return []

            # 获取所有邮件ID
            email_ids = message_ids[0].split()

            # 获取上次检查时记录的邮件数量
            current_count = len(email_ids)

            # 如果是第一次运行，记录当前邮件数量并返回空列表
            if not hasattr(self, 'last_email_count'):
                self.last_email_count = current_count
                print(f"初始化邮件监听，当前共有 {current_count} 封邮件")
                return []

            # 计算新增邮件数量
            new_count = current_count - self.last_email_count

            if new_count > 0:
                print(f"检测到 {new_count} 封新邮件")
                # 获取新邮件的ID（最新的邮件在列表末尾）
                new_email_ids = email_ids[-new_count:]

                new_emails = []
                for email_id in new_email_ids:
                    # 获取邮件
                    status, msg_data = self.mail.fetch(email_id, '(RFC822)')
                    if status != 'OK':
                        continue

                    # 解析邮件
                    raw_email = msg_data[0][1]
                    email_message = email.message_from_bytes(raw_email)

                    # 提取邮件信息
                    subject = self.decode_subject(email_message['Subject'])
                    sender = email_message['From']
                    date = email_message['Date']

                    # 解析邮件内容
                    content = self.get_email_content(email_message)

                    email_info = {
                        'id': email_id.decode(),
                        'subject': subject,
                        'from': sender,
                        'date': date,
                        'content': content
                    }

                    new_emails.append(email_info)

                    # 标记为已读（可选）
                    # self.mail.store(email_id, '+FLAGS', '\\Seen')

                # 更新邮件计数
                self.last_email_count = current_count
                return new_emails
            else:
                # 更新邮件计数（可能有邮件被删除）
                self.last_email_count = current_count
                return []

        except Exception as e:
            print(f"检查新邮件时出错: {e}")
            return []

    def decode_subject(self, subject):
        """解码邮件主题"""
        if subject:
            decoded_fragments = decode_header(subject)
            decoded_subject = ""
            for fragment, encoding in decoded_fragments:
                if isinstance(fragment, bytes):
                    if encoding:
                        # 处理未知编码问题
                        try:
                            decoded_subject += fragment.decode(encoding)
                        except:
                            # 如果编码未知，尝试使用utf-8或latin1
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

    def get_email_content(self, email_message):
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
                                # 处理未知编码问题
                                try:
                                    content = payload.decode(charset)
                                except:
                                    # 如果编码未知，尝试使用utf-8或latin1
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
                        print(f"解码邮件内容时出错: {e}")
                        pass
        else:
            try:
                payload = email_message.get_payload(decode=True)
                if payload:
                    charset = email_message.get_content_charset()
                    if charset:
                        # 处理未知编码问题
                        try:
                            content = payload.decode(charset)
                        except:
                            # 如果编码未知，尝试使用utf-8或latin1
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
                print(f"解码邮件内容时出错: {e}")
                pass
        return content

    def listen_for_emails(self, check_interval=60):
        """监听新邮件（生成器版本，适配原始接口）"""
        if not self.connect():
            return

        print("开始监听新邮件...")

        try:
            while not self.should_stop:
                with self.mail_lock:  # 使用锁来确保线程安全
                    new_emails = self.check_new_emails()

                if new_emails:
                    print(f"\n发现 {len(new_emails)} 封新邮件!")
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
                        # 将邮件放入队列
                        self.email_queue.put(new_email)

                # 如果队列中有邮件，逐个返回
                while not self.email_queue.empty():
                    yield self.email_queue.get()

                time.sleep(check_interval)

        except KeyboardInterrupt:
            print("\n停止监听")
        except Exception as e:
            print(f"监听过程中出错: {e}")
        finally:
            # self.disconnect()
            ...
    def stop_listening(self):
        """停止监听"""
        self.should_stop = True



