import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

class ScannerScreen extends StatefulWidget {
  const ScannerScreen({super.key});

  @override
  State<ScannerScreen> createState() => _ScannerScreenState();
}

class _ScannerScreenState extends State<ScannerScreen> {
  String? _scannedCode;

  @override
  Widget build(BuildContext context) {
    if (_scannedCode != null) {
      // 스캔 완료 후 결과 화면
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.check_circle, size: 60, color: Colors.green),
            const SizedBox(height: 16),
            Text('스캔된 코드: $_scannedCode', style: const TextStyle(fontSize: 18)),
            const SizedBox(height: 8),
            const Text(
              '(나중에 서버 연동되면 이 코드로 설비 상세정보를 조회합니다)',
              style: TextStyle(color: Colors.grey, fontSize: 12),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () {
                setState(() {
                  _scannedCode = null; // 다시 스캔하기
                });
              },
              child: const Text('다시 스캔하기'),
            ),
          ],
        ),
      );
    }

    // 카메라 스캔 화면
    return MobileScanner(
      onDetect: (capture) {
        final List<Barcode> barcodes = capture.barcodes;
        if (barcodes.isNotEmpty && barcodes.first.rawValue != null) {
          setState(() {
            _scannedCode = barcodes.first.rawValue;
          });
        }
      },
    );
  }
}