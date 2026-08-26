import 'package:flutter/material.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;
import 'package:printing/printing.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'dart:convert';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final TextEditingController _ipController = TextEditingController(text: '192.168.0.13');

  @override
  void initState() {
    super.initState();
    _loadIp();
  }

  Future<void> _loadIp() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString('server_ip');
    if (saved != null) _ipController.text = saved;
  }

  // 파이에서 오늘 점검 기록 실제로 불러오기
  Future<List<Map<String, String>>> _fetchReportData() async {
    final prefs = await SharedPreferences.getInstance();
    final ip = prefs.getString('server_ip') ?? '192.168.0.13';
    try {
      final res = await http
          .get(Uri.parse('http://$ip:8000/api/inspections/today'))
          .timeout(const Duration(seconds: 5));
      if (res.statusCode == 200) {
        final List<dynamic> data = jsonDecode(res.body);
        return data.map<Map<String, String>>((e) => {
              '설비': e['equipment_id'].toString(),
              '상태': e['status'].toString(),
              '점검자': e['inspector'].toString(),
              '시각': e['timestamp'].toString(),
            }).toList();
      }
    } catch (e) {
      // 실패하면 빈 리스트 반환, 아래에서 처리
    }
    return [];
  }

  Future<void> _generatePdf() async {
    final reportData = await _fetchReportData();

    if (!mounted) return;
    if (reportData.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('오늘 점검 완료된 설비가 없습니다')),
      );
      return;
    }

    final pdf = pw.Document();
    pdf.addPage(
      pw.Page(
        build: (context) {
          return pw.Column(
            crossAxisAlignment: pw.CrossAxisAlignment.start,
            children: [
              pw.Text('불침번 — 점검이력 리포트',
                  style: pw.TextStyle(fontSize: 20, fontWeight: pw.FontWeight.bold)),
              pw.SizedBox(height: 8),
              pw.Text('생성일: ${DateTime.now().toString().split('.')[0]}'),
              pw.SizedBox(height: 20),
              pw.Table.fromTextArray(
                headers: ['설비', '상태', '점검자', '시각'],
                data: reportData
                    .map((row) => [row['설비']!, row['상태']!, row['점검자']!, row['시각']!])
                    .toList(),
              ),
            ],
          );
        },
      ),
    );

    await Printing.layoutPdf(
      onLayout: (PdfPageFormat format) async => pdf.save(),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('서버 IP 주소', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          TextField(
            controller: _ipController,
            decoration: const InputDecoration(
              border: OutlineInputBorder(),
              hintText: '예: 192.168.0.13',
            ),
          ),
          const SizedBox(height: 16),
          ElevatedButton(
            onPressed: () async {
              final prefs = await SharedPreferences.getInstance();
              await prefs.setString('server_ip', _ipController.text);
              if (!mounted) return;
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text('저장됨: ${_ipController.text}')),
              );
            },
            child: const Text('저장'),
          ),
          const SizedBox(height: 32),
          const Divider(),
          const SizedBox(height: 16),
          const Text('점검이력 리포트', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          ElevatedButton.icon(
            onPressed: _generatePdf,
            icon: const Icon(Icons.picture_as_pdf),
            label: const Text('PDF 생성'),
          ),
        ],
      ),
    );
  }
}