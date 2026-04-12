import sys
import os
import unittest

# Adiciona o diretório src ao path para os testes funcionarem
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from processor import calculate_metrics

class TestProcessor(unittest.TestCase):
    def setUp(self):
        # Mock de dados da API Henrik v3
        self.mock_match = {
            'metadata': {
                'map': 'Ascent',
                'rounds_played': 20,
                'mode': 'Competitive'
            },
            'players': {
                'all_players': [
                    {
                        'name': 'TestPlayer',
                        'tag': '123',
                        'team': 'Red',
                        'character': 'Jett',
                        'stats': {
                            'kills': 20,
                            'deaths': 10,
                            'assists': 5,
                            'score': 4000
                        },
                        'damage_made': 3000
                    }
                ]
            },
            'teams': {
                'red': {'has_won': True},
                'blue': {'has_won': False}
            }
        }

    def test_calculate_metrics_success(self):
        metrics = calculate_metrics(self.mock_match, 'TestPlayer')
        
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics['kills'], 20)
        self.assertEqual(metrics['adr'], 150.0) # 3000 / 20 rounds
        self.assertEqual(metrics['kd'], 2.0)    # 20 / 10 deaths
        self.assertEqual(metrics['was_win'], 1)
        self.assertEqual(metrics['agent'], 'Jett')

    def test_calculate_metrics_player_not_found(self):
        metrics = calculate_metrics(self.mock_match, 'Unknown')
        self.assertIsNone(metrics)

    def test_calculate_metrics_zero_rounds(self):
        self.mock_match['metadata']['rounds_played'] = 0
        metrics = calculate_metrics(self.mock_match, 'TestPlayer')
        self.assertEqual(metrics['adr'], 0.0) # Deve lidar com divisão por zero

if __name__ == '__main__':
    unittest.main()
