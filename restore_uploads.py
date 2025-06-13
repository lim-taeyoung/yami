# restore_uploads.py
import os
import shutil

def restore_upload_folder():
    src = 'backup/uploads'
    dst = 'static/uploads'
    if not os.path.exists(dst):
        os.makedirs(dst)
    for filename in os.listdir(src):
        full_src = os.path.join(src, filename)
        full_dst = os.path.join(dst, filename)
        shutil.copy(full_src, full_dst)
        print(f"복원됨: {filename}")

if __name__ == "__main__":
    restore_upload_folder()
