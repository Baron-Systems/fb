# Frappe Manager Backup Dashboard (fb)

**نظام نسخ احتياطي بدون إعداد لمواقع Frappe Manager.**

Zero-config backup system for Frappe Manager sites.

---

## المميزات / Features

- 🎯 **بدون إعداد:** شغّل `fb run` فقط — كل شيء يُعد تلقائياً
- 🔐 **آمن:** توقيع HMAC، حماية CSRF، سجل تدقيق
- 📊 **متعدد الوكلاء:** إدارة النسخ الاحتياطية عبر عدة سيرفرات إنتاج
- ⏰ **مجدول:** نسخ احتياطية يومية تلقائية مع سياسات الاحتفاظ
- 🌐 **واجهة ويب:** واجهة واضحة مبنية بـ Jinja2 + HTMX
- 📦 **تخزين:** هيكل منظم: `/backups/<agent>/<stack>/<site>/<timestamp>/`
- 🔍 **سجل تدقيق:** كل إجراء مسجّل وقابل للتتبع

---

## البدء السريع / Quick Start

```bash
# تثبيت
pipx install git+https://github.com/Baron-Systems/fb.git

# تشغيل (يبدأ على المنفذ 7311)
fb run
```

افتح: `http://localhost:7311`

---

## المتطلبات / Requirements

- Python 3.11+
- الوكيل (`fb-agent`) يعمل على سيرفرات الإنتاج

---

## البنية / Architecture

**لوحة التحكم تعمل كـ:**

- واجهة إدارة مركزية
- منسق نسخ احتياطية
- خادم تخزين
- مجدول (APScheduler)
- سجل وكلاء متعدد

**أولوية التخزين:**

1. `/srv/backups` (معيار الإنتاج)
2. `/backups` (إن كان قابلاً للكتابة)
3. `~/.local/share/fb/backups` (احتياطي)

---

## خدمة systemd / systemd Service

```bash
# تثبيت كخدمة
sudo curl -o /etc/systemd/system/fb-dashboard.service \
  https://raw.githubusercontent.com/Baron-Systems/fb/main/fb-dashboard.service

sudo systemctl daemon-reload
sudo systemctl enable --now fb-dashboard
```

---

## المنافذ والبيانات / Ports & Data

| العنصر | القيمة |
|--------|--------|
| المنفذ | 7311 (HTTP) |
| الاكتشاف | 7310 (UDP broadcast) |
| قاعدة البيانات | `~/.local/share/fb/fb.sqlite3` |
| النسخ الاحتياطية | `/srv/backups/` أو `~/.local/share/fb/backups/` |

---

## التطوير / Development

```bash
git clone https://github.com/Baron-Systems/fb.git
cd fb

pip install -e .
python -m fb.cli
```

---

## التوثيق / Documentation

- [دليل التثبيت](INSTALL.md)
- [التحسينات المطبقة](../ENHANCEMENTS_APPLIED.md) (إن وُجد)
- [تقرير المراجعة](../REVIEW.md) (إن وُجد)

---

## استكشاف الأخطاء / Troubleshooting

### الوكيل لا يتصل باللوحة

1. تأكد أن اللوحة تعمل: `curl http://dashboard_ip:7311/`
2. راجع قواعد الجدار الناري
3. راجع سجلات الوكيل: `journalctl -u fb-agent -n 50`
4. أضف الوكيل يدوياً من واجهة اللوحة إن فشل الاكتشاف التلقائي

### النسخ الاحتياطية لا تعمل

1. تأكد أن الموقع يعمل: `fm list`
2. شغّل الموقع: `fm start <site_name>`
3. راجع سجلات الوكيل
4. راجع سجل التدقيق في واجهة اللوحة

---

## الترخيص / License

Proprietary

## الدعم / Support

Issues: https://github.com/Baron-Systems/fb/issues
