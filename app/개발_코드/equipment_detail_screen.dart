import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class EquipmentDetailScreen extends StatefulWidget {
  final String equipmentId;
  const EquipmentDetailScreen({super.key, required this.equipmentId});

  @override
  State<EquipmentDetailScreen> createState() => _EquipmentDetailScreenState();
}

class _EquipmentDetailScreenState extends State<EquipmentDetailScreen> {
  late final WebViewController _controller;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadWebView();
  }

  Future<void> _loadWebView() async {
    final prefs = await SharedPreferences.getInstance();
    final ip = prefs.getString('server_ip') ?? '192.168.0.13';
    _controller = WebViewController()
      ..loadRequest(Uri.parse('http://$ip:5000/'));  // 통짜 대시보드, 루트 경로
    setState(() => _loading = false);
  }

  Future<void> _completeInspection() async {
    final prefs = await SharedPreferences.getInstance();
    final ip = prefs.getString('server_ip') ?? '192.168.0.13';

    final confirm = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('점검 완료 처리하시겠습니까?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('확인')),
        ],
      ),
    );
    if (confirm != true) return;

    try {
      final res = await http.post(
        Uri.parse('http://$ip:8000/api/equipment/${widget.equipmentId}/inspection'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'inspector': '현서', 'status': 'normal'}),
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(res.statusCode == 200 ? '저장됨' : '실패: ${res.statusCode}')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('연결 실패: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.equipmentId)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                Expanded(child: WebViewWidget(controller: _controller)),
                Container(
                  width: double.infinity,
                  color: Colors.deepOrange.shade50,
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  child: Text(
                    '현재 보고 있는 설비: ${widget.equipmentId}',
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: SizedBox(
                    width: double.infinity,
                    child: ElevatedButton(
                      onPressed: _completeInspection,
                      child: const Text('점검 완료'),
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}