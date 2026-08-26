import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'dart:convert';
import 'equipment_detail_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  List<Map<String, String>> _modules = [];
  bool _loading = true;
  String? _errorMessage;

  // 서버 연결 안 될 때 보여줄 기본값 (데모 중 파이가 꺼져도 화면은 뜨게)
  final List<Map<String, String>> _fallbackModules = const [
    {'id': 'detector1_zone_a', 'name': '자탐1 (차동식)', 'status': 'normal'},
    {'id': 'detector2_zone_b', 'name': '자탐2 (광전식)', 'status': 'normal'},
    {'id': 'pump_01', 'name': '수계', 'status': 'warning'},
    {'id': 'gas_tank_01', 'name': '가스계', 'status': 'normal'},
    {'id': 'extinguisher_04', 'name': '소화기', 'status': 'normal'},
    {'id': 'evac_light_03', 'name': '유도등', 'status': 'critical'},
  ];

  @override
  void initState() {
    super.initState();
    _fetchStatus();
  }

  Future<void> _fetchStatus() async {
    setState(() { _loading = true; _errorMessage = null; });
    try {
      final prefs = await SharedPreferences.getInstance();
      final ip = prefs.getString('server_ip') ?? '192.168.0.13';

      final res = await http
          .get(Uri.parse('http://$ip:8000/api/equipment/checklist'))
          .timeout(const Duration(seconds: 5));

      if (res.statusCode == 200) {
        final List<dynamic> data = jsonDecode(res.body);
        setState(() {
          _modules = data.map<Map<String, String>>((e) => {
                'id': e['equipment_id'].toString(),
                'name': e['edge_type'].toString(),
                'status': e['status'].toString(),
              }).toList();
          _loading = false;
        });
      } else {
        throw Exception('서버 응답 오류: ${res.statusCode}');
      }
    } catch (e) {
      // 서버 연결 실패 → 임시 데이터로 폴백, 상단에 경고 배너만 표시
      setState(() {
        _modules = _fallbackModules;
        _errorMessage = '서버 연결 실패 — 임시 데이터 표시 중 (설정에서 IP 확인)';
        _loading = false;
      });
    }
  }

  Color _statusColor(String status) {
    switch (status) {
      case 'normal':
        return Colors.green;
      case 'warning':
        return Colors.orange;
      case 'critical':
      case 'alarm':
        return Colors.red;
      default:
        return Colors.grey;
    }
  }

  String _statusLabel(String status) {
    switch (status) {
      case 'normal':
        return '정상';
      case 'warning':
        return '주의';
      case 'critical':
      case 'alarm':
        return '경보';
      default:
        return '알 수 없음';
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }

    return RefreshIndicator(
      onRefresh: _fetchStatus,
      child: Column(
        children: [
          if (_errorMessage != null)
            Container(
              width: double.infinity,
              color: Colors.orange.shade100,
              padding: const EdgeInsets.all(8),
              child: Text(_errorMessage!, style: const TextStyle(fontSize: 12)),
            ),
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: _modules.length,
              itemBuilder: (context, index) {
                final module = _modules[index];
                final status = module['status']!;
                return Card(
                  margin: const EdgeInsets.symmetric(vertical: 6),
                  child: ListTile(
                    leading: CircleAvatar(
                      backgroundColor: _statusColor(status),
                      child: const Icon(Icons.circle, color: Colors.transparent),
                    ),
                    title: Text(module['name']!),
                    trailing: Text(
                      _statusLabel(status),
                      style: TextStyle(
                        color: _statusColor(status),
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    onTap: () {
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => EquipmentDetailScreen(
                            equipmentId: module['id']!,
                          ),
                        ),
                      ).then((_) => _fetchStatus()); // 돌아오면 상태 새로고침
                    },
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}