import imaplib
import email
import asyncio
import time
import os
from email.header import decode_header

class QQEmailListener:
    def __init__(self, email_address, password):
        self.email_address = email_address
        self.password = password
        self.imap_server = "imap.qq.com"
        self.imap_port = 993
        
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
            self.mail.close()
            self.mail.logout()
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
        """监听新邮件（同步版本）"""
        if not self.connect():
            return
            
        print("开始监听新邮件...")
        
        try:
            while True:
                new_emails = self.check_new_emails()
                new_email=dict()
                if new_emails:
                    print(f"\n发现 {len(new_emails)} 封新邮件!")
                    for email_info in new_emails:
                        new_email["email_id"] = email_info['id']
                        new_email["sender_email"] = email_info['from']
                        new_email['email_content'] = {
                            "主题": email_info['subject'],
                            "内容预览": email_info['content'][:100],
                            "日期": email_info['date']
                        }

                time.sleep(check_interval)
                if new_email:
                    yield new_email
                else:
                    continue
                
        except KeyboardInterrupt:
            print("\n停止监听")
        except Exception as e:
            print(f"监听过程中出错: {e}")
        finally:
            self.disconnect()
            
    async def async_listen_for_emails(self, check_interval=60):
        """监听新邮件（异步版本）"""
        if not self.connect():
            return
            
        print("开始异步监听新邮件...")
        
        try:
            while True:
                new_emails = self.check_new_emails()
                new_email=dict()
                if new_emails:

                    print(f"\n发现 {len(new_emails)} 封新邮件!")
                    for email_info in new_emails:
                        new_email["email_id"] = email_info['id']
                        new_email["sender_email"] = email_info['from']
                        new_email['email_content'] = {
                            "主题": email_info['subject'],
                            "内容": email_info['content'][:100],
                            "日期": email_info['date']
                        }
                # 使用asyncio.sleep而不是time.sleep，允许事件循环处理其他任务
                await asyncio.sleep(check_interval)
                if new_email:
                    yield new_email
                else:
                    continue
                
        except KeyboardInterrupt:
            print("\n停止监听")
        except Exception as e:
            print(f"监听过程中出错: {e}")
        finally:
            self.disconnect()

# 异步监听函数
async def async_main():
    # 从环境变量获取邮箱信息
    email_address = os.getenv('QQEMAIL')
    password = os.getenv('EMAIL_PASSWORD')  # QQ邮箱需要使用授权码而非密码
    
    if not email_address or not password:
        print("请设置环境变量 QQEMAIL 和 EMAIL_PASSWORD")
        return
        
    listener = QQEmailListener(email_address, password)
    await listener.async_listen_for_emails(check_interval=30)  # 每30秒检查一次

# 同步监听函数
def main():
    # 从环境变量获取邮箱信息
    email_address = os.getenv('QQEMAIL')
    password = os.getenv('EMAIL_PASSWORD')  # QQ邮箱需要使用授权码而非密码
    
    if not email_address or not password:
        print("请设置环境变量 QQEMAIL 和 EMAIL_PASSWORD")
        return
        
    listener = QQEmailListener(email_address, password)
    listener.listen_for_emails(check_interval=30)  # 每30秒检查一次

if __name__ == "__main__":
    # 运行同步版本
    # main()
    
    # 运行异步版本
    asyncio.run(async_main())