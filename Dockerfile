FROM python:3.9
WORKDIR /app
EXPOSE 8080

RUN pip install pipenv
COPY Pipfile Pipfile.lock ./
RUN pipenv install
COPY . ./

CMD pipenv run manage autorun