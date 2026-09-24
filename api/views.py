from rest_framework.response import Response
from rest_framework.views import APIView


class ApiRootView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(
            {
                'name': 'TRUEC@ API',
                'version': 'v1',
                'status': 'foundation',
            }
        )
