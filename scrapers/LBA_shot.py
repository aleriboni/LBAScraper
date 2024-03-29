import logging
from datetime import datetime
import json
import math
import re
from datetime import timedelta
import pandas as pd
from bs4 import BeautifulSoup
import requests as requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3 import Retry
import utils
from scraper import Scraper


class LBAScraperShot(Scraper):

    def __init__(self):
        super().__init__()

    def get_seasons(self, **kwargs):
        def find_code(year, seasons):
            for season in seasons:
                if year == season['year']:
                    return season['id']

        seasons = dict()

        base_url = 'https://www.legabasket.it/championship/'
        rs_url = f'{base_url}/429'
        po_url = f'{base_url}/222'

        analyzed_years = set()
        rs_seasons = json.loads(requests.get(rs_url).content)
        rs_seasons = sorted(rs_seasons['data']['years'], key=lambda d: (d['year'], d['id']))

        for season in rs_seasons:
            if season['year'] in analyzed_years:
                continue
            analyzed_years.add(season['year'])

            season_code = f'{season["year"]}-{season["year"] + 1}'

            if 'seasons' in kwargs and kwargs['seasons'] and season_code not in kwargs['seasons']:
                continue

            seasons[season['year']] = dict()

            seasons[season['year']]['year'] = season['year']
            seasons[season['year']]['code'] = season_code

            seasons[season['year']]['RS'] = find_code(season['year'], rs_seasons)

            po_seasons = json.loads(requests.get(po_url).content)['data']['years']
            seasons[season['year']]['PO'] = find_code(season['year'], po_seasons)

        return seasons

    def get_games(self, season, **kwargs):

        games = []

        base_url = 'https://www.legabasket.it/championship/'
        league_url = 'https://www.legabasket.it/phase/'

        params = dict()

        params['s'] = season['year']
        params['c'] = season['RS']

        url = f'{base_url}{season["RS"]}'
        season_full = json.loads(requests.get(url).content)['data']
        rs_phases = sorted(season_full['phases'], key=lambda d: d['id'])
        for phase in rs_phases:
            phase_name = self.map_phase(phase["code"])
            params['p'] = phase['id']
            rounds = json.loads(
                requests.get(f'{league_url}{phase["id"]}/{season["RS"]}').content.decode(
                    'utf8'))['data']['days']

            for r in rounds:
                params['d'] = r['code']
                game_url = 'https://www.legabasket.it/lba/6/calendario/calendar?'
                soup = utils.get_soup(game_url, params=params)

                while soup is None:
                    soup = utils.get_soup(url)

                if soup.find('tbody') is None:
                    continue

                for tr in soup.find('tbody').find_all('tr'):
                    url_id = tr.find(class_='result').find('a').attrs['href']
                    game_id = re.findall(r'/game/([0-9]*)/*', url_id)[0]

                    game_result = tr.find(class_='result').text.strip()
                    status = 'played' if game_result != '0 - 0' else 'scheduled'

                    if status != 'played':
                        continue

                    try:
                        date = datetime.strptime(':'.join(tr.find_all('td')[5].text.strip().split()),
                                                 '%d/%m/%Y:%H:%M')
                    except ValueError:
                        print("Error fetching date")
                        continue

                    dataset = f'{season["code"]} {phase_name}'

                    game = {
                        'game_id': game_id,
                        'data_set': dataset,
                        'date': date
                    }

                    games.append(game)

        if season['PO'] is None:
            return games
        params['c'] = season['PO']

        url = f'{base_url}{season["PO"]}'
        data = json.loads(requests.get(url).content)['data']

        po_phases = sorted(data['phases'], key=lambda d: d['id'])
        for phase in po_phases:
            phase_name = self.map_phase(phase["code"])
            params['p'] = phase['id']
            rounds = json.loads(
                requests.get(f'{league_url}{phase["id"]}/{season["PO"]}').content.decode(
                    'utf8'))['data']['days']

            for r in rounds:
                params['d'] = r['code']
                game_url = 'https://www.legabasket.it/lba/6/calendario/calendar?'
                soup = utils.get_soup(game_url, params=params)

                while soup is None:
                    soup = utils.get_soup(url)

                if soup.find('tbody') is None:
                    continue

                for tr in soup.find('tbody').find_all('tr'):
                    url_id = tr.find(class_='result').find('a').attrs['href']
                    game_id = re.findall(r'/game/([0-9]*)/*', url_id)[0]

                    game_result = tr.find(class_='result').text.strip()
                    status = 'played' if game_result != '0 - 0' else 'scheduled'

                    if status != 'played':
                        continue

                    try:
                        date = datetime.strptime(':'.join(tr.find_all('td')[5].text.strip().split()),
                                                 '%d/%m/%Y:%H:%M')
                    except ValueError:
                        continue

                    dataset = f'{season["code"]} {phase_name}'

                    game = {
                        'game_id': game_id,
                        'data_set': dataset,
                        'date': date
                    }

                    games.append(game)

        return games

    def get_actions(self):
        game = self.current_game
        url = f'https://www.legabasket.it/match/{game["game_id"]}/pbp'
        actions = []

        session = requests.Session()
        retry = Retry(connect=10, backoff_factor=2)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        period = 1
        period_url = f'{url}/{period}/ASC'
        try:
            response = session.get(period_url).json()
        except json.decoder.JSONDecodeError:
            print(f"Actions not found for game {game} @ {period_url}")
            return actions
        while response['data']['pbp'] is not None and response['data']['pbp']:
            actions += response['data']['pbp']
            period += 1
            period_url = f'{url}/{period}/ASC'
            try:
                response = session.get(period_url).json()
            except json.decoder.JSONDecodeError:
                continue

        return actions

    def get_boxes(self, soup: BeautifulSoup):

        boxes = dict()

        scores = soup.find('div', id='scores')

        if scores is None:
            return dict

        for h5 in scores.find_all('h5'):
            team = h5.text

            table = h5.find_next('tbody')

            boxes[team] = dict()
            boxes[team]['players'] = []
            boxes[team]['team'] = []

            player_re = re.compile(r'^tr_player_(\d+)$', re.IGNORECASE)
            total_re = re.compile(r'^tr_totals_(\d+)$', re.IGNORECASE)
            for row in table.find_all('tr'):
                if player_re.match(row.attrs['id']):

                    mapping = self.get_stats_mapping()
                    tds = row.find_all('td')
                    stats = dict()
                    stats['Team'] = team

                    name = tds[0].find('span', {'class': 'scores_player_name'}).text
                    surname = tds[0].find('span', {'class': 'scores_player_surname'}).text

                    stats['Player'] = ' '.join([name, surname]).title().strip()
                    for key in mapping:
                        stats[key] = int(tds[mapping[key]].text)

                    boxes[team]['players'].append(stats.copy())

                elif total_re.match(row.attrs['id']):
                    mapping = self.get_stats_mapping(team=True)
                    tds = row.find_all('td')
                    stats = dict()
                    stats['Team'] = team

                    for key in mapping:
                        stats[key] = int(tds[mapping[key]].text)

                    boxes[team]['team'].append(stats.copy())

        for t1 in boxes:
            for t2 in boxes:
                if t1 != t2:
                    boxes[t1]['opponent'] = [boxes[t2]['team'][0].copy()]
                    boxes[t1]['opponent'][0]['Team'] = t1

        for t in boxes:
            boxes[t]['team'][0]['PM'] = boxes[t]['team'][0]['PTS'] - boxes[t]['opponent'][0]['PTS']
            boxes[t]['opponent'][0]['PM'] = boxes[t]['opponent'][0]['PTS'] - boxes[t]['team'][0]['PTS']

        return boxes

    def get_stats_mapping(self, team=False):
        mapping = {
            'MIN': 2,
            'PTS': 1,
            'P2M': 6,
            'P2A': 7,
            'P3M': 10,
            'P3A': 11,
            'FTM': 13,
            'FTA': 14,
            'OREB': 16,
            'DREB': 17,
            'AST': 23,
            'TOV': 21,
            'STL': 22,
            'BLK': 19,
            'PF': 4,
            'PM': 26,
        }

        if team:
            mapping.pop('PM')

        return mapping

    def get_starters(self, soup):
        # url = f'https://www.legabasket.it/game/{self.current_game["game_id"]}/scores'
        # soup = utils.get_soup(url)

        starters = dict()
        starters['home'] = []
        starters['away'] = []

        scores_div = soup.find('div', id='scores')

        if scores_div is None:
            return starters

        table = scores_div.find('table', id='ht_match_scores').find_next('tbody')
        for tr in table.find_all('tr'):
            if tr.find_all_next('td')[3].find('i'):
                name = tr.find_all_next('td')[0].find('span', {'class': 'scores_player_name'}).text.title()
                surname = tr.find_all_next('td')[0].find('span', {'class': 'scores_player_surname'}).text.title()

                starters['home'].append(' '.join([name, surname]))

        table = scores_div.find('table', id='vt_match_scores').find_next('tbody')
        for tr in table.find_all('tr'):
            if tr.find_all_next('td')[3].find('i'):
                name = tr.find_all_next('td')[0].find('span', {'class': 'scores_player_name'}).text.title()
                surname = tr.find_all_next('td')[0].find('span', {'class': 'scores_player_surname'}).text.title()

                starters['away'].append(' '.join([name, surname]))

        if not starters:
            print(f"Could not find starter in game {self.current_game['game_id']}")
            print(table)
            exit(1)
        return starters

    def remove_substitutions(self, raw_actions):

        actions = []
        for raw_action in raw_actions:
            if raw_action['description'] not in ['Ingresso', 'Uscita']:
                actions.append(raw_action)
        return actions

    def add_ft_count(self, raw_actions):

        player = None
        num = 0

        for raw_action in raw_actions:
            if raw_action['description'].lower() in {'tiro libero sbagliato', 'tiro libero segnato'}:
                player_ra = ' '.join([raw_action['player_name'], raw_action['player_surname']]).title()
                if player is None or player_ra != player:
                    player = player_ra
                    num = 1
                    raw_action['num'] = num
                else:
                    num += 1
                    raw_action['num'] = num
            else:
                player = None
                num = 0

        player = None
        outof = 0

        for raw_action in raw_actions[::-1]:
            if raw_action['description'].lower() in {'tiro libero sbagliato', 'tiro libero segnato'}:
                player_ra = ' '.join([raw_action['player_name'], raw_action['player_surname']]).title()
                if player is None or player_ra != player:
                    player = player_ra
                    outof = raw_action['num']
                    raw_action['outof'] = outof
                else:
                    raw_action['outof'] = outof
            else:
                player = None
                outof = 0

        return raw_actions

    def clean_actions(self, raw_actions):

        #pd.DataFrame(raw_actions).to_csv('test.csv')
        raw_actions = self.remove_substitutions(raw_actions)
        raw_actions = self.add_ft_count(raw_actions)

        actions = []

        home_score = 0
        away_score = 0

        action_start = timedelta(minutes=0)
        period_start = 1

        for raw_action in raw_actions:

            action = dict()

            action['game_id'] = self.current_game['game_id']

            action['data_set'] = self.current_game['data_set']
            action['date'] = self.current_game['date']

            period = raw_action['period']
            action['period'] = period

            # score is in the format 22 - 18 (home - away)
            if raw_action['score']:
                home_score = int(raw_action['score'].split('-')[0])
                away_score = int(raw_action['score'].split('-')[1])

            action['home_score'] = home_score
            action['away_score'] = away_score

            # print(raw_action['minute'], raw_action['seconds'])

            elapsed_time = timedelta(minutes=raw_action['minute'], seconds=raw_action['seconds'])
            elapsed_hours, elapsed_remainder = divmod(elapsed_time.seconds, 3600)
            elapsed_minutes, elapsed_seconds = divmod(elapsed_remainder, 60)
            elapsed_str = f'{elapsed_minutes:02d}:{elapsed_seconds:02d}'

            period_minutes = 10 if raw_action['period'] <= 4 else 5
            time_duration = timedelta(minutes=period_minutes)

            remaining_time = time_duration - elapsed_time
            remaining_hours, remaining_remainder = divmod(remaining_time.seconds, 3600)
            remaining_minutes, remaining_seconds = divmod(remaining_remainder, 60)
            remaining_str = f'{remaining_minutes:02d}:{remaining_seconds:02d}'

            action['remaining_time'] = remaining_str
            action['elapsed_time'] = elapsed_str

            if period != period_start:
                action_start = timedelta(minutes=0)
                period_start = period
            play_length = elapsed_time - action_start
            play_length_hours, play_length_remainder = divmod(play_length.seconds, 3600)
            play_length_minutes, play_length_seconds = divmod(play_length_remainder, 60)
            play_length_str = f'{play_length_minutes:02d}:{play_length_seconds:02d}'
            action['play_length'] = play_length_str
            action_start = elapsed_time

            action['play_id'] = raw_action['action_id']

            action['team'] = raw_action['team_name']

            event_type = self.map_event_type(raw_action['description'])

            if event_type is None and raw_action['description'].lower() == "assist":
                actions[-1]['assist'] = ' '.join(
                    [raw_action['player_name'], raw_action['player_surname']]).title().strip()
                continue
            elif event_type is None and raw_action['description'].lower() == "stoppata":
                actions[-1]['block'] = ' '.join(
                    [raw_action['player_name'], raw_action['player_surname']]).title().strip()
                continue
            elif event_type is None and raw_action['description'].lower() == "fallo subito":
                actions[-1]['opponent'] = ' '.join(
                    [raw_action['player_name'], raw_action['player_surname']]).title().strip()
                continue
            elif event_type is None and raw_action['description'].lower() == "palla recuperata":
                actions[-1]['steal'] = ' '.join(
                    [raw_action['player_name'], raw_action['player_surname']]).title().strip()
                continue
            elif event_type is None and raw_action['description'].lower() == "stoppata subita":
                continue

            action['event_type'] = event_type

            action['assist'] = ''

            if event_type == 'jump ball' and raw_action['home_club'] and raw_action['player_name'] and raw_action[
                'player_surname']:
                action['away'] = ''
                action['home'] = ' '.join([raw_action['player_name'], raw_action['player_surname']]).title().strip()
            elif event_type == 'jump ball' and not raw_action['home_club'] and raw_action['player_name'] and raw_action[
                'player_surname']:
                action['away'] = ' '.join([raw_action['player_name'], raw_action['player_surname']]).title().strip()
                action['home'] = ''
            else:
                action['away'] = ''
                action['home'] = ''

            action['block'] = ''

            if event_type == 'sub':
                action['entered'] = raw_action['player_in']
                action['left'] = raw_action['player_out']
            else:
                action['entered'] = ''
                action['left'] = ''

            if 'num' in raw_action:
                action['num'] = raw_action['num']
            else:
                action['num'] = None

            action['opponent'] = ''

            if 'outof' in raw_action:
                action['outof'] = raw_action['outof']
            else:
                action['outof'] = None

            if raw_action['player_name'] and raw_action['player_surname']:
                action['player'] = ' '.join([raw_action['player_name'], raw_action['player_surname']]).title().strip()
            else:
                action['player'] = ''

            points = self.map_points(raw_action['description'])
            action['points'] = points

            action['possession'] = ''

            action['reason'] = self.map_reason(
                [raw_action["action_1_qualifier_description"], raw_action["action_2_qualifier_description"]])

            if points is not None and points > 0:
                action['result'] = 'made'
            elif points is not None and points == 0:
                action['result'] = 'missed'
            else:
                action['result'] = ''

            action['steal'] = ''

            action['type'] = self.map_type(raw_action)

            # original coordinates place the origin in the bottom left corner. The coordinate span is (0, 100) for both axis, so we shall divide by the number of feet of the size
            if raw_action['x'] and raw_action['y']:
                x = (raw_action['y'] - 50) * .15
                y = (raw_action['x'] - 50) * .28

                original_x = raw_action['x']
                original_y = raw_action['y']

                if x >= 0:
                    x = -x
                    y = -y
                # y += (14 - 1.575)

                converted_y = y
                converted_x = x

                converted_y = x
                converted_x = y

                # left side
                if raw_action['side'] == 0:  # and raw_action['side_area_zone'] == 'A':
                    x_rim = 5.17

                else:
                    x_rim = 91.86 - 5.17

                y_rim = 49.21 / 2
                shot_distance = math.sqrt((converted_x - x_rim) ** 2 + (converted_y - y_rim) ** 2)
            else:
                original_x = None
                original_y = None
                converted_x = None
                converted_y = None
                shot_distance = None

            action['shot_distance'] = shot_distance
            action['original_x'] = original_x
            action['original_y'] = original_y
            action['converted_x'] = converted_x
            action['converted_y'] = converted_y

            action['description'] = raw_action['description']

            actions.append(action)

        return actions

    def get_tadd(self, season_id):
        url = 'https://www.legabasket.it/lba/6/calendario/standings'
        params = {'s': season_id}

        soup = utils.get_soup(url, params=params)
        table = soup.find('table', class_='full-standings')
        tbody = table.find('tbody')

        df = pd.DataFrame(columns=['Team', 'team', 'Conference', 'Division', 'Rank', 'Playoff'])

        for tr in tbody.find_all('tr'):
            tds = tr.find_all('td')

            rank = int(tds[0].text.strip())

            df = pd.concat([df, pd.DataFrame([{
                'Team': tds[1].text.strip(),
                'team': '',
                'Conference': '',
                'Division': '',
                'Rank': rank,
                'Playoff': 'Y' if rank <= 8 else 'N',
            }])], ignore_index=True)

        return df.sort_values(by=['Team'])

    def download_data(self, **kwargs):
        dataframes = dict()
        seasons = self.get_seasons(**kwargs)

        for season in seasons:
            dataframes[season] = dict()

            players_df = pd.DataFrame(
                columns=['Team', 'Player', 'MIN', 'PTS', 'P2M', 'P2A', 'P3M', 'P3A', 'FTM', 'FTA', 'OREB', 'DREB',
                         'AST', 'TOV', 'STL', 'BLK', 'PF', 'PM'])
            team_df = pd.DataFrame(
                columns=['Team', 'MIN', 'PTS', 'P2M', 'P2A', 'P3M', 'P3A', 'FTM', 'FTA', 'OREB', 'DREB', 'AST', 'TOV',
                         'STL', 'BLK', 'PF', 'PM'])
            opponent_df = pd.DataFrame(
                columns=['Team', 'MIN', 'PTS', 'P2M', 'P2A', 'P3M', 'P3A', 'FTM', 'FTA', 'OREB', 'DREB', 'AST', 'TOV',
                         'STL', 'BLK', 'PF', 'PM'])
            pbp_df = pd.DataFrame(
                columns=['game_id', 'data_set', 'date', 'a1', 'a2', 'a3', 'a4', 'a5', 'h1', 'h2', 'h3', 'h4', 'h5',
                         'period', 'home_score', 'away_score', 'remaining_time', 'elapsed_time', 'play_length',
                         'play_id', 'team', 'event_type', 'assist', 'away', 'home', 'block', 'entered', 'left', 'num',
                         'opponent', 'outof', 'player', 'points', 'possession', 'reason', 'result', 'steal', 'type',
                         'shot_distance', 'original_x', 'original_y', 'converted_x', 'converted_y', 'description'])

            tadd_df = self.get_tadd(season_id=season)
            # tadd_df = pd.DataFrame(tadd, columns=['Team', 'team', 'Conference', 'Division', 'Rank', 'Playoff'])

            games = self.get_games(seasons[season])
            # games = [{'game_id': '24064', 'data_set': 'RS', 'date': datetime.today()}]

            for game in tqdm(games):

                self.current_game = game
                url = f'https://www.legabasket.it/game/{game["game_id"]}'

                soup = utils.get_soup(url)

                self.starters = self.get_starters(soup)

                boxes = self.get_boxes(soup)

                if not boxes:
                    continue

                if type(boxes) == type:
                    print(self.current_game['game_id'])

                for team in boxes:
                    players_df = pd.concat([players_df, pd.DataFrame(boxes[team]['players'])], ignore_index=True)
                    team_df = pd.concat([team_df, pd.DataFrame(boxes[team]['team'])], ignore_index=True)
                    opponent_df = pd.concat([opponent_df, pd.DataFrame(boxes[team]['opponent'])], ignore_index=True)

                if kwargs['ignore_pbp']:
                    continue

                raw_actions = self.get_actions()
                if not raw_actions:
                    print(f"Missing play-by-play logs for game {game}")
                    continue
                elif game['game_id'] in {}:
                    print(f"Game play-by-play is faulted, ignoring. {game}")
                    continue
                actions = self.clean_actions(raw_actions)

                pbp_df = pd.concat([pbp_df, pd.DataFrame(actions)], ignore_index=True)

            dataframes[season]['Pbox'] = self.summarize_players_df(players_df)
            dataframes[season]['Tbox'] = self.summarize_teams_df(team_df)
            dataframes[season]['Obox'] = self.summarize_teams_df(opponent_df, opponent=True)
            dataframes[season]['PBP'] = pbp_df
            dataframes[season]['Tadd'] = tadd_df

        return dataframes

    def map_event_type(self, description):
        mapping = {
            'substitution': 'sub',
            'falli di squadra': 'foul',
            'fallo commesso': 'foul',
            'palla contesa': 'jump ball',
            'palla persa': 'turnover',
            'palle perse di squadra': 'turnover',
            'rimbalzo difensivo': 'rebound',
            'rimbalzi difensivi di squadra': 'rebound',
            'rimbalzo offensivo': 'rebound',
            'rimbalzi offensivi di squadra': 'rebound',
            'tiro libero sbagliato': 'free throw',
            'tiro libero segnato': 'free throw',
            '2 punti sbagliato': 'miss',
            '2 punti segnato': 'shot',
            '3 punti sbagliato': 'miss',
            '3 punti segnato': 'shot',
            'inizio tempo': 'start of period',
            'fine tempo': 'end of period',
            'time out': 'timeout',
        }

        if description.lower() in mapping:
            return mapping[description.lower()]
        else:
            return None

    def map_points(self, description):
        mapping = {
            'tiro libero sbagliato': 0,
            'tiro libero segnato': 1,
            '2 punti sbagliato': 0,
            '2 punti segnato': 2,
            '3 punti sbagliato': 0,
            '3 punti segnato': 3,

        }

        if description.lower() in mapping:
            return mapping[description.lower()]
        else:
            return None

    def map_reason(self, descriptions):
        mapping = {
            '3 secondi': '3 second violation',
            '5 secondi': '5 second violation',
            '8 secondi': '8 second violation',
            'antisportivo': 'flagrant foul',
            'antisportivo su tiro': 'shooting flagrant foul',
            'doppio': 'double dribble turnover',
            'doppio palleggio': 'discontinue dribble turnover',
            'espulsione': 'ejection',
            'fuori dal campo': 'out of bounds lost ball turnover',
            'infrazione di campo': 'backcourt',
            'offensivo': 'offensive foul',
            'palleggio': 'lost ball',
            'passaggio sbagliato': 'bad pass',
            'passi': 'traveling',
            'personale': 'personal foul',
            'tecnico': 'techincal foul',
            'tecnico allenatore': 'coach technical foul',
            'tiro': 'shooting foul',
            'violazione 24sec': 'shot clock violation',
        }
        for description in descriptions:
            if description and description.lower() in mapping:
                return mapping[description.lower()]
        return ''

    def map_type(self, entry):

        mapping_description = {
            'palla contesa': 'jump ball',
            # 'palla persa': 'turnover',
            # 'palle perse di squadra': 'TOV',
            'rimbalzo difensivo': 'rebound defensive',
            'rimbalzi difensivi di squadra': 'team rebound',
            'rimbalzo offensivo': 'rebound offensive',
            'rimbalzi offensivi di squadra': 'team rebound',

            'inizio tempo': 'start of period',
            'fine tempo': 'end of period',
            'time out': 'timeout: regular',
        }

        mapping_flags = {
            '3 secondi': '3 second violation',
            '5 secondi': '5 second violation',
            '8 secondi': '8 second violation',
            'antisportivo': 'flagrant foul',
            'antisportivo su tiro': 'shooting flagrant foul',
            'doppio': 'double dribble turnover',
            'doppio palleggio': 'discontinue dribble turnover',
            'espulsione': 'ejection',
            'fuori dal campo': 'out of bounds lost ball turnover',
            'infrazione di campo': 'backcourt',
            'offensivo': 'offensive foul',
            'palleggio': 'lost ball',
            'passaggio sbagliato': 'bad pass',
            'passi': 'traveling',
            'personale': 'personal foul',
            'tecnico': 'techincal foul',
            'tecnico allenatore': 'coach technical foul',
            'tiro': 'shooting foul',
            'violazione 24sec': 'shot clock violation',
            # 'alley-oop': 'ALLEY-OOP',
            'altro': None,
            'appoggio a canestro': 'Layup',
            'arresto e tiro': 'Pullup',
            'da penetrazione': 'Driving',
            'gancio': 'Hook Shot',
            'giro e tiro': 'Turnaround shot',
            'schiacciata': 'Dunk',
            'stoppata': None,
            'tiro in corsa': 'Floating Jump Shot',
            'tiro in fadeaway': 'Fadeaway Jumper',
            'tiro in sospensione': 'Jump Shot',
            'tiro in step back': 'Step Back Jump Shot',
        }

        if entry['description'].lower() in mapping_description:
            return mapping_description[entry['description'].lower()]

        if entry['description'].lower() in ['tiro libero segnato', 'tiro libero sbagliato']:
            return f'Free Throw {entry["num"]} of {entry["outof"]}'

        for el in [entry['action_1_qualifier_description'], entry['action_2_qualifier_description']]:
            if el and el.lower() == 'alley-oop':
                if entry['dunk']:
                    return 'Alley Oop Dunk'
                else:
                    return 'Alley Oop Layup'

        for description in [entry['action_1_qualifier_description'], entry['action_2_qualifier_description']]:
            if description and description.lower() in mapping_flags:
                return mapping_flags[description.lower()]

        return ''

    def map_phase(self, code):
        mapping = {
            'andata': 'Regular Season',
            'ritorno': 'Regular Season',
            'seconda fase': 'Clock Round',
            'ottavi': 'Playoffs',
            'quarti': 'Playoffs',
            'quarti di finale': 'Playoffs',
            'semifinali': 'Playoffs',
            'finale': 'Playoffs',
            'finali': 'Playoffs',
            'girone a': 'Regular Season',
            'girone b': 'Regular Season',
            'girone c': 'Regular Season',
            'girone d': 'Regular Season',
            'Finale 3°/4° Posto': 'Playoffs',
        }

        if code.lower() in mapping:
            return mapping[code.lower()]
        else:
            print(f"{code} not recognized in allowed values")
            return None