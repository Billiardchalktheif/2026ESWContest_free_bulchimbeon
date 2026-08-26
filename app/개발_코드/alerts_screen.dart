import 'package:flutter/material.dart';

class AlertsScreen extends StatelessWidget {
  const AlertsScreen({super.key});

  // 나중에 /api/events 연동되면 이 가짜 데이터 대신 실제 값이 들어감
  final List<Map<String, String>> _alerts = const [
    {'module': '유도등', 'message': '배터리 전압 저하 감지', 'time': '10분 전'},
    {'module': '수계', 'message': '충압펌프 기동빈도 증가', 'time': '1시간 전'},
  ];

  @override
  Widget build(BuildContext context) {
    if (_alerts.isEmpty) {
      return const Center(child: Text('현재 경보 없음'));
    }
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: _alerts.length,
      itemBuilder: (context, index) {
        final alert = _alerts[index];
        return Card(
          margin: const EdgeInsets.symmetric(vertical: 6),
          child: ListTile(
            leading: const Icon(Icons.warning_amber, color: Colors.orange),
            title: Text('${alert['module']} — ${alert['message']}'),
            subtitle: Text(alert['time']!),
            trailing: TextButton(
              onPressed: () {
                // 나중에 /api/alert/ack 연동 예정
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('확인 처리됨 (임시)')),
                );
              },
              child: const Text('확인'),
            ),
          ),
        );
      },
    );
  }
}